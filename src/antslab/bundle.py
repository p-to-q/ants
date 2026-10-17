"""Sealing bundle: a read-only, verifiable snapshot of one or more ledgers.

`ants bundle make` creates a directory with compressed ledger snapshots, a MANIFEST.json,
checksums.sha256, SEAL.md and a .tar.gz archive.  `ants bundle verify` checks everything.

The bundle is private until scrubbed (roadmap step 24): ledgers hold hints and agent tool outputs.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from .ledger import Ledger, LedgerTooNew, SCHEMA_VERSION


class BundleError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_readonly(ledger_path: Path, dest: Path) -> Path:
    """Take a consistent SQLite snapshot without modifying the source.

    Opens the source read-only (mode=ro), uses the backup API to copy it
    to a temporary file, then returns the path to the snapshot.
    """
    if not ledger_path.exists():
        raise BundleError(f"ledger not found: {ledger_path}")
    def attempt(uri_tail: str):
        src = sqlite3.connect(f"file:{ledger_path}?mode=ro{uri_tail}", uri=True, timeout=30)
        try:
            out = sqlite3.connect(dest)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
    try:
        attempt("")
    except sqlite3.OperationalError as e:
        # a ledger at rest in a read-only directory (an archive) cannot create its -shm file: read it as immutable, which is right only when
        # nothing is writing it. A busy or locked file is NOT retried that way (a live writer would be read inconsistently).
        if "locked" in str(e) or "busy" in str(e):
            raise BundleError(f"{ledger_path} is in use by a writer: stop it first ({e})") from e
        dest.unlink(missing_ok=True)
        try:
            attempt("&immutable=1")
        except sqlite3.OperationalError as e2:
            raise BundleError(f"cannot open {ledger_path} read-only: {e2}") from e2
    return dest


def _inspect_ledger(path: Path) -> dict:
    """Open a snapshot READ-ONLY and gather metadata. The snapshot is the sealed record: it is not migrated, checkpointed or written in any way
    (an older-schema ledger stays at its schema; make_bundle checks that its bytes are unchanged afterwards)."""
    try:
        led = Ledger.open_readonly(path)
    except LedgerTooNew as e:
        return {"error": f"LedgerTooNew: {e}", "schema_version": -1}
    try:
        info = led.check(full=True)
        head = led.head()
        seq = led.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        chain_ok, chain_bad = led.verify_chain()

        # trial counts by (stage, state)
        trial_counts = {}
        for r in led.conn.execute("SELECT stage, state, COUNT(*) FROM trials GROUP BY stage, state"):
            trial_counts[f"{r[0]}:{r[1]}"] = r[2]

        events_count = led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        # first/last event timestamps
        first_ts = led.conn.execute("SELECT MIN(ts) FROM events").fetchone()[0]
        last_ts = led.conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]

        # total cpu_s
        total_cpu = led.conn.execute("SELECT COALESCE(SUM(cpu_s), 0) FROM trials").fetchone()[0]

        # epochs table
        epochs = []
        for r in led.conn.execute("SELECT id, ts, info FROM epochs ORDER BY ts"):
            epochs.append({"id": r[0], "ts": r[1], "info": json.loads(r[2])})

        # daemon_started events: distinct (arm, arm_def_hash, version, epoch)
        starts = []
        seen = set()
        for ev in led.events("daemon_started"):
            p = ev["payload"]
            key = (p.get("arm", ""), p.get("arm_def_hash", ""),
                   p.get("version", ""), p.get("epoch", ""))
            if key not in seen:
                seen.add(key)
                starts.append({"arm": key[0], "arm_def_hash": key[1],
                               "version": key[2], "epoch": key[3]})

        led.close()

        return {
            "schema_version": info["schema_version"],
            "trial_counts": trial_counts,
            "events_count": events_count,
            "head": head,
            "chain_ok": chain_ok,
            "chain_bad_seq": chain_bad,
            "first_event_ts": first_ts,
            "last_event_ts": last_ts,
            "total_cpu_s": round(total_cpu, 2),
            "epochs": epochs,
            "daemon_starts": starts,
            "integrity_ok": info["integrity_ok"],
        }
    except Exception as e:
        try:
            led.close()
        except Exception:
            pass
        raise BundleError(f"inspecting snapshot: {e}") from e


def make_bundle(name: str, out_dir: str | Path, workdirs: list[str | Path],
                note_file: str | Path | None = None) -> dict:
    """Create a sealed bundle from one or more workdirs."""
    from .update import current_version

    out = Path(out_dir)
    bundle_dir = out / name
    ledgers_dir = bundle_dir / "ledgers"
    ledgers_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "bundle_format": 1,
        "name": name,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ants_version": current_version(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ledgers": {},
    }

    for wd_path in workdirs:
        wd = Path(wd_path)
        ledger_path = wd / "ledger.sqlite"
        wd_name = wd.name

        # snapshot to a temp file, then gzip into the bundle
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td) / "snap.sqlite"
            _snapshot_readonly(ledger_path, snap)
            raw_sha = _sha256(snap)
            raw_bytes = snap.stat().st_size

            gz_path = ledgers_dir / f"{wd_name}.sqlite.gz"
            with open(snap, "rb") as fi, open(gz_path, "wb") as raw_out, gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, compresslevel=6, mtime=0) as fo:
                shutil.copyfileobj(fi, fo)              # the record first, exactly as the snapshot came out of the backup API (no timestamp in the gz header)

            info = _inspect_ledger(snap)                # then look at it (read-only) for the manifest
            if "error" in info:
                raise BundleError(f"{wd_name}: {info['error']}")
            if _sha256(snap) != raw_sha:
                raise BundleError(f"{wd_name}: the snapshot changed while it was inspected (this is a bug; nothing was sealed)")

        gz_sha = _sha256(gz_path)
        gz_bytes = gz_path.stat().st_size

        manifest["ledgers"][wd_name] = {
            "gz_sha256": gz_sha,
            "raw_sha256": raw_sha,
            "gz_bytes": gz_bytes,
            "raw_bytes": raw_bytes,
            **info,
        }

    # DEPLOY.log: copy if present beside the workdirs
    if workdirs:
        parent = Path(workdirs[0]).parent
        deploy_log = parent / "DEPLOY.log"
        if deploy_log.exists():
            shutil.copy2(deploy_log, bundle_dir / "DEPLOY.log")

    # MANIFEST.json
    manifest_path = bundle_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    # SEAL.md
    note_text = ""
    if note_file:
        note_text = Path(note_file).read_text()
    seal = _generate_seal(name, manifest, note_text)
    (bundle_dir / "SEAL.md").write_text(seal)

    # checksums.sha256 (every file in the bundle except itself)
    checksums = _compute_checksums(bundle_dir, exclude={"checksums.sha256"})
    checksum_lines = []
    for rel, sha in sorted(checksums.items()):
        checksum_lines.append(f"{sha}  {rel}")
    (bundle_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n")

    # tar.gz with deterministic properties
    tar_path = out / f"{name}.tar.gz"
    _make_tar(bundle_dir, tar_path, name)
    tar_sha = _sha256(tar_path)
    Path(str(tar_path) + ".sha256").write_text(f"{tar_sha}  {tar_path.name}\n")
    print(f"bundle: {tar_path}")
    print(f"sha256: {tar_sha}")

    return {"tar": str(tar_path), "sha256": tar_sha, "manifest": manifest}


def _compute_checksums(base: Path, exclude: set[str] | None = None) -> dict[str, str]:
    """Compute sha256 of every file under base, relative paths as keys."""
    out = {}
    for f in sorted(base.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(base))
            if exclude and rel in exclude:
                continue
            out[rel] = _sha256(f)
    return out


def _generate_seal(name: str, manifest: dict, note_text: str) -> str:
    lines = [
        f"# Sealed bundle: {name}",
        "",
        f"Created: {manifest['created_utc']}",
        f"ants version: {manifest['ants_version']}",
        f"Python: {manifest['python']}",
        f"Platform: {manifest['platform']}",
        "",
        "## Contents",
        "",
    ]
    for wd_name, info in manifest["ledgers"].items():
        lines.append(f"- **{wd_name}**: {info.get('events_count', '?')} events, "
                     f"{sum(v for v in info.get('trial_counts', {}).values())} trials, "
                     f"{info.get('total_cpu_s', '?')} CPU-seconds, "
                     f"chain {'intact' if info.get('chain_ok') else 'BROKEN'}")
    lines.extend([
        "",
        "## Verification",
        "",
        "```bash",
        f"ants bundle verify {name}/",
        f"# or from the archive:",
        f"ants bundle verify {name}.tar.gz",
        "```",
        "",
        "## Integrity",
        "",
        "This bundle is a **read-only record** and must **never be edited**.",
        "`ants bundle verify` detects accidental damage and any edit that does not also recompute the chain, the counts, the manifest and the checksums.",
        "It does **not** detect a deliberate, self-consistent rewrite (every event re-hashed, the manifest and checksums regenerated): the chain is unkeyed. "
        "That is detectable only against a chain head or an anchor that was stored somewhere the writer cannot reach (see docs/evaluation/E0-integrity.md and the roadmap, step 29). "
        "Store the head of each ledger (listed in MANIFEST.json) and the sha256 of the .tar.gz off this machine when the bundle is made.",
        "",
        "## Privacy",
        "",
        "This bundle is **private until scrubbed**: ledgers hold hints and agent tool",
        "outputs that may contain information not intended for publication.",
        "See roadmap step 24 for the scrubbing procedure.",
        "",
    ])
    if note_text:
        lines.extend(["## Notes", "", note_text, ""])
    return "\n".join(lines)


def _make_tar(src_dir: Path, tar_path: Path, prefix: str):
    """Create a deterministic-enough tar.gz: sorted entries, fixed mtimes."""
    mtime = 1700000000  # a fixed reference time
    with tarfile.open(tar_path, "w:gz") as tf:
        for f in sorted(src_dir.rglob("*")):
            arcname = f"{prefix}/{f.relative_to(src_dir)}"
            info = tf.gettarinfo(str(f), arcname)
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if f.is_file():
                with open(f, "rb") as fh:
                    tf.addfile(info, fh)
            else:
                tf.addfile(info)


def _safe_extract(tf: tarfile.TarFile, dest: str) -> None:
    """Extract only regular files and directories whose names stay inside `dest` (a bundle to verify may come from anywhere)."""
    root = Path(dest).resolve()
    for m in tf.getmembers():
        target = (root / m.name).resolve()
        if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in Path(m.name).parts or root not in (target, *target.parents):
            raise BundleError(f"unsafe entry in the archive: {m.name!r}")
    try:
        tf.extractall(dest, members=tf.getmembers(), filter="data")      # Python 3.12+ (and the 3.11.4+ backport): no links, no absolute names
    except TypeError:
        tf.extractall(dest, members=tf.getmembers())                      # older: the members were checked above


def verify_bundle(path: str | Path) -> int:
    """Verify a bundle (directory or tar.gz).  Returns 0 if everything checks out."""
    path = Path(path)
    tmp_dir = None
    bundle_dir = path

    if path.is_file() and path.name.endswith(".tar.gz"):
        tmp_dir = tempfile.mkdtemp()
        try:
            with tarfile.open(path, "r:gz") as tf:
                _safe_extract(tf, tmp_dir)
        except Exception as e:
            print(f"FAIL: cannot extract {path}: {e}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 1
        # find the bundle directory inside
        entries = list(Path(tmp_dir).iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            bundle_dir = entries[0]
        else:
            bundle_dir = Path(tmp_dir)

    try:
        return _verify_dir(bundle_dir)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _verify_dir(bundle_dir: Path) -> int:
    problems = []

    # 1. checksums.sha256
    checksum_file = bundle_dir / "checksums.sha256"
    if not checksum_file.exists():
        print("FAIL: checksums.sha256 not found")
        return 1

    expected = {}
    for line in checksum_file.read_text().strip().splitlines():
        sha, rel = line.split("  ", 1)
        expected[rel] = sha

    actual = _compute_checksums(bundle_dir, exclude={"checksums.sha256"})

    for rel, sha in expected.items():
        if rel not in actual:
            problems.append(f"MISSING: {rel}")
        elif actual[rel] != sha:
            problems.append(f"CHECKSUM MISMATCH: {rel}")
    for rel in actual:
        if rel not in expected:
            problems.append(f"UNEXPECTED FILE: {rel}")

    # 2. MANIFEST.json
    manifest_path = bundle_dir / "MANIFEST.json"
    if not manifest_path.exists():
        problems.append("MANIFEST.json not found")
        _report(problems)
        return 1

    manifest = json.loads(manifest_path.read_text())

    # 3. verify each ledger
    for wd_name, info in manifest.get("ledgers", {}).items():
        gz_path = bundle_dir / "ledgers" / f"{wd_name}.sqlite.gz"
        if not gz_path.exists():
            problems.append(f"{wd_name}: gz file missing")
            continue

        # check gz sha
        gz_sha = _sha256(gz_path)
        if gz_sha != info.get("gz_sha256"):
            problems.append(f"{wd_name}: gz sha256 mismatch (expected {info.get('gz_sha256', '?')[:12]}, got {gz_sha[:12]})")
            continue

        # gunzip to temp, check raw sha
        with tempfile.TemporaryDirectory() as td:
            raw_path = Path(td) / "ledger.sqlite"
            try:
                with gzip.open(gz_path, "rb") as fi, open(raw_path, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
            except Exception as e:
                problems.append(f"{wd_name}: gunzip failed: {e}")
                continue

            raw_sha = _sha256(raw_path)
            if raw_sha != info.get("raw_sha256"):
                problems.append(f"{wd_name}: raw sha256 mismatch")
                continue

            # open READ-ONLY, check schema, chain, counts, head
            try:
                led = Ledger.open_readonly(raw_path)
            except LedgerTooNew as e:
                problems.append(f"{wd_name}: LedgerTooNew: {e}")
                continue
            except Exception as e:
                problems.append(f"{wd_name}: cannot open: {e}")
                continue

            try:
                sv = led.schema_version()
                if sv != info.get("schema_version"):
                    problems.append(f"{wd_name}: schema version {sv} != manifest {info.get('schema_version')}")

                ok, bad = led.verify_chain()
                if not ok:
                    problems.append(f"{wd_name}: chain broken at seq {bad}")
                if ok != info.get("chain_ok"):
                    problems.append(f"{wd_name}: chain_ok disagrees with manifest")

                head = led.head()
                if head != info.get("head"):
                    problems.append(f"{wd_name}: head {head[:12]} != manifest {info.get('head', '?')[:12]}")

                events_count = led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if events_count != info.get("events_count"):
                    problems.append(f"{wd_name}: events count {events_count} != manifest {info.get('events_count')}")

                # trial counts
                actual_counts = {}
                for r in led.conn.execute("SELECT stage, state, COUNT(*) FROM trials GROUP BY stage, state"):
                    actual_counts[f"{r[0]}:{r[1]}"] = r[2]
                if actual_counts != info.get("trial_counts", {}):
                    problems.append(f"{wd_name}: trial counts differ from manifest")
            finally:
                led.close()

    _report(problems)
    return 1 if problems else 0


def _report(problems: list[str]):
    if problems:
        print("--- verification FAILED ---")
        for p in problems:
            print(f"  {p}")
    else:
        print("--- verification passed ---")
