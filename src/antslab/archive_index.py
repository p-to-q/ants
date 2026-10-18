"""Small, immutable per-round summaries; the terminal reads only the cached index.

Indexing is an explicit offline operation over a verified bundle. It never opens
an active ledger, and snapshots deliberately omit research result summaries.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import statistics
import tarfile
import tempfile
import time

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
MAX_BYTES = 2 << 20


def _json(raw):
    def reject(value):
        raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(raw, parse_constant=reject)


def index_path() -> Path:
    from .home import home
    if os.environ.get("ANTS_ARCHIVE_INDEX"):
        return Path(os.environ["ANTS_ARCHIVE_INDEX"]).expanduser()
    personal = home() / "archive" / "index.json"
    return personal if personal.exists() else Path("/var/lib/ants/archive-index/index.json")


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("archive cache must be a regular file of at most 2 MiB")
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("archive cache grew beyond 2 MiB")
    return data


def read_index(path: str | Path | None = None) -> dict:
    path = Path(path) if path else index_path()
    try:
        data = _json(_read(path))
        if data.get("format") != 1 or not isinstance(data.get("rounds"), list):
            raise ValueError("unsupported archive index format")
        if len(data["rounds"]) > 2000:
            raise ValueError("archive index exceeds 2000 rounds")
        seen = set()
        for row in data["rounds"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not SAFE_ID.fullmatch(row["id"]):
                raise ValueError("invalid archive round identity")
            if row["id"] in seen:
                raise ValueError("duplicate archive round identity")
            seen.add(row["id"])
            if not isinstance(row.get("preview", []), list) or any(not isinstance(p, dict) for p in row.get("preview", [])):
                raise ValueError("invalid archive preview")
            checked = row.get("verified_at")
            if checked is not None and (isinstance(checked, bool) or not isinstance(checked, (int, float)) or not math.isfinite(checked) or not 0 <= checked <= 253402300799):
                raise ValueError("invalid archive verification time")
            for result in row.get("preview", []):
                for key in ("n", "total"):
                    if not isinstance(result.get(key), int) or result[key] < 0:
                        raise ValueError("invalid archive coverage")
                if result["n"] > result["total"]:
                    raise ValueError("invalid archive coverage")
                value = result.get("median")
                if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise ValueError("invalid archive metric")
        return {**data, "path": str(path)}
    except FileNotFoundError:
        return {"format": 1, "rounds": [], "path": str(path)}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"format": 1, "rounds": [], "path": str(path), "error": str(exc)}


def read_round(catalog: dict, row: dict) -> dict:
    name = row.get("id", "")
    if not isinstance(name, str) or not SAFE_ID.fullmatch(name):
        raise ValueError("invalid archive round id")
    raw = _read(Path(catalog["path"]).parent / (name + ".json"))
    if hashlib.sha256(raw).hexdigest() != row["summary_sha256"]:
        raise ValueError("archive summary checksum mismatch")
    data = _json(raw)
    if not isinstance(data, dict) or data.get("format") != 1 or data.get("id") != name:
        raise ValueError("archive summary identity mismatch")
    return data


def _encode(data: dict) -> bytes:
    raw = (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("archive cache exceeds 2 MiB")
    return raw


def _atomic(path: Path, raw: bytes):
    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _run_summary(path: Path, contract, info: dict, kind: str, expected: str) -> dict:
    epochs = info.get("epochs", [])
    epoch = max(epochs, key=lambda x: x["ts"])["id"] if epochs else ""
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        arm_row = conn.execute("SELECT arm,seed FROM trials WHERE epoch=? ORDER BY id DESC LIMIT 1", (epoch,)).fetchone()
        arm, seed = arm_row if arm_row else (path.stem, None)
        total, cpu = conn.execute("SELECT COUNT(*),COALESCE(SUM(cpu_s),0) FROM trials WHERE epoch=?", (epoch,)).fetchone()
        out = {"arm": arm, "seed": seed, "epoch": epoch, "trials": total, "cpu_s": cpu,
               "head": info["head"], "best": {}}
        if kind == "snapshot":
            return out
        if epoch != expected:
            raise ValueError(f"archive epoch {epoch} differs from contract/source {expected}; use its frozen source")
        sign = 1 if contract.direction == "min" else -1
        for tid, key, params, metrics in conn.execute(
                "SELECT id,spec_key,params,metrics FROM trials WHERE epoch=? AND arm=? AND stage=? AND state='done'",
                (epoch, arm, contract.stages[-1])):
            try:
                metric = json.loads(metrics)
                value, count = float(metric[contract.primary]), float(metric[contract.cap_metric])
                if not math.isfinite(value) or not math.isfinite(count) or count < 0:
                    continue
                for cap in contract.caps:
                    if count <= cap and (str(cap) not in out["best"] or sign * value < sign * out["best"][str(cap)]["value"]):
                        out["best"][str(cap)] = {"value": value, "n_params": count, "trial": tid,
                                                 "key": key, "params": json.loads(params)}
            except (ValueError, TypeError, KeyError):
                continue
        return out
    finally:
        conn.close()


def build_index(bundle: str | Path, contract_path: str | Path, cache: str | Path,
                title: str = "", kind: str = "snapshot", offsite_note: str = "", location: str = "") -> dict:
    """Verify a frozen bundle, then append one immutable round and atomically update its index."""
    if kind not in ("pilot", "completed", "aborted", "snapshot"):
        raise ValueError("invalid archive kind")
    from .bundle import _safe_extract, _sha256, verify_bundle
    from .contract import load_contract
    from .series import compute_epoch
    contract = load_contract(contract_path)
    expected = compute_epoch(contract_path)
    with tempfile.TemporaryDirectory(prefix="ants-index-") as td:
        root = Path(td)
        frozen = root / "input.tar.gz"
        shutil.copyfile(bundle, frozen)       # subsequent verification and extraction use these same private bytes
        if verify_bundle(frozen) != 0:
            raise ValueError("bundle verification failed; no index written")
        extract = root / "extracted"
        extract.mkdir()
        with tarfile.open(frozen, "r:gz") as archive:
            _safe_extract(archive, extract)
        dirs = list(extract.iterdir())
        source = dirs[0] if len(dirs) == 1 and dirs[0].is_dir() else extract
        manifest = json.loads((source / "MANIFEST.json").read_text())
        name = manifest["name"]
        if not SAFE_ID.fullmatch(name):
            raise ValueError("invalid archive round id")
        runs = []
        for ledger_name, info in manifest["ledgers"].items():
            if not SAFE_ID.fullmatch(ledger_name):
                raise ValueError("invalid ledger name")
            raw = root / (ledger_name + ".sqlite")
            with gzip.open(source / "ledgers" / (ledger_name + ".sqlite.gz"), "rb") as fi, raw.open("wb") as fo:
                shutil.copyfileobj(fi, fo)
            runs.append(_run_summary(raw, contract, info, kind, expected))
        groups = {}
        for run in runs:
            groups.setdefault((run["arm"], run["epoch"]), []).append(run)
        focus = next((cap for cap in contract.caps if cap == 300), contract.caps[0])
        preview = []
        for (arm, epoch), group in sorted(groups.items()):
            values = [r["best"][str(focus)]["value"] for r in group if str(focus) in r["best"]]
            preview.append({"arm": arm, "epoch": epoch, "n": len(values), "total": len(group),
                            "median": statistics.median(values) if values else None})
        summary = {"format": 1, "id": name, "title": title or name, "kind": kind,
                   "created_utc": manifest["created_utc"], "verified_at": time.time(),
                   "bundle_sha256": _sha256(frozen), "bundle_location": location or str(Path(bundle).resolve()),
                   "offsite_note": offsite_note, "metric": contract.primary, "stage": contract.stages[-1],
                   "task": contract.name,
                   "summary_policy": "Descriptive medians of observed per-run best search-stage values. Missing values are counted and excluded, not imputed. This is not the confirmatory analysis or a cross-round ranking.",
                   "direction": contract.direction, "caps": list(contract.caps), "focus_cap": focus,
                   "preview": preview, "runs": runs}
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".index.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        catalog = read_index(cache / "index.json")
        if catalog.get("error"):
            raise ValueError(catalog["error"])
        old = next((r for r in catalog["rounds"] if r["id"] == name), None)
        if old:
            previous = read_round(catalog, old)
            if previous["bundle_sha256"] != summary["bundle_sha256"]:
                raise ValueError("round id already refers to a different bundle; archived rounds are immutable")
            return old
        summary_path = cache / (name + ".json")
        if summary_path.exists():
            raw = _read(summary_path)
            previous = _json(raw)
            if not isinstance(previous, dict) or previous.get("id") != name or previous.get("bundle_sha256") != summary["bundle_sha256"]:
                raise ValueError("an unindexed summary already uses this round id; refusing to overwrite it")
            summary = previous
        else:
            raw = _encode(summary)
            _atomic(summary_path, raw)
        row = {k: v for k, v in summary.items() if k != "runs"}
        row["summary_sha256"] = hashlib.sha256(raw).hexdigest()
        rounds = sorted([row, *catalog["rounds"]], key=lambda r: (r["created_utc"], r["id"]), reverse=True)
        if len(rounds) > 2000:
            raise ValueError("archive index exceeds 2000 rounds; no index replaced")
        _atomic(cache / "index.json", _encode({"format": 1, "rounds": rounds}))
    return row
