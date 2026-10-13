"""Backups of the ledger that are consistent, verified, and restorable. Standard library only.

A backup is made with SQLite's online backup API, so the daemon keeps running and the copy is a consistent snapshot (never a torn
file copy of a database with a live WAL). It is only kept if it then passes SQLite's integrity check and the event-chain check,
and it is described by a small sidecar file (size, sha256, event count, chain head, schema version) so that a restore can be
verified before it replaces anything. The chain head is also appended to `anchors.jsonl`: copy that file, or the whole directory,
off the machine, because an anchor kept next to the ledger cannot expose a rewritten ledger.

    <dest>/ledger-20261013-031500.sqlite[.gz]     the snapshot
    <dest>/ledger-20261013-031500.json            sidecar: sha256, bytes, seq, head, schema_version, trials, created
    <dest>/anchors.jsonl                          one line per backup: {"ts", "seq", "head"} (what Ledger.verify_anchor_file reads)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .ledger import Ledger, LedgerTooNew  # noqa: F401  (LedgerTooNew re-exported for callers that restore)

STAMP = "%Y%m%d-%H%M%S"


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _claim_name(dest: Path, t: float) -> str:
    """a backup name nobody else is using: the sidecar is created exclusively, so concurrent backups in the same second get different names"""
    base = "ledger-" + time.strftime(STAMP, time.localtime(t))
    for n in range(100):
        name = base if n == 0 else f"{base}-{n}"
        try:
            os.close(os.open(dest / (name + ".json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))      # placeholder: list_backups skips it until it is real
            return name
        except FileExistsError:
            continue
    raise BackupError(f"could not find a free backup name in {dest}")


def make_backup(ledger_path, dest, keep: int = 14, compress: bool = False, now: float | None = None) -> dict:
    """Snapshot `ledger_path` into `dest`; returns the sidecar dict. Raises BackupError (and keeps nothing) if the copy does not verify."""
    ledger_path, dest = Path(ledger_path), Path(dest)
    if not ledger_path.exists():
        raise BackupError(f"no ledger at {ledger_path}")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(dest).free
        need = ledger_path.stat().st_size * 2 + (64 << 20)
        if free < need:
            raise BackupError(f"only {free >> 20} MB free in {dest}; a backup of this ledger needs about {need >> 20} MB")
        t = time.time() if now is None else now
        name = _claim_name(dest, t)
    except OSError as e:
        raise BackupError(f"cannot write to {dest}: {e}") from e
    tmp = dest / f"{name}.{os.getpid()}.partial"
    final = None
    try:
        try:
            src = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True, timeout=30)
            try:
                out = sqlite3.connect(tmp)
                try:
                    src.backup(out)                                   # consistent online snapshot
                finally:
                    out.close()
            finally:
                src.close()
            led = Ledger(tmp)                                         # opens read-write; the copy is ours and still private
            try:
                info = led.check(full=True)
                head = led.head()
                seq = led.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
                led.checkpoint()
            finally:
                led.close()
        except (sqlite3.Error, LedgerTooNew) as e:
            raise BackupError(f"the snapshot could not be made or opened: {type(e).__name__}: {e}") from e
        if not info["integrity_ok"] or not info["chain_ok"]:
            raise BackupError(f"the snapshot failed verification (integrity={info['integrity']}, chain_ok={info['chain_ok']}); nothing was kept")
        for ext in ("-wal", "-shm"):
            Path(str(tmp) + ext).unlink(missing_ok=True)
        sha, size = _sha256(tmp), tmp.stat().st_size
        final = dest / (name + (".sqlite.gz" if compress else ".sqlite"))
        if compress:
            with open(tmp, "rb") as fi, gzip.open(str(final) + ".partial", "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(str(final) + ".partial", final)
        else:
            os.replace(tmp, final)
        meta = {"file": final.name, "created": t, "sha256": sha, "bytes": size, "stored_bytes": final.stat().st_size, "compressed": compress,
                "schema_version": info["schema_version"], "trials": info["trials"], "events": info["events"], "seq": seq, "head": head,
                "source": str(ledger_path.resolve())}
        side = dest / (name + ".json")
        part = side.with_name(side.name + ".partial")
        part.write_text(json.dumps(meta, indent=1))
        os.replace(part, side)
        with open(dest / "anchors.jsonl", "a") as f:
            f.write(json.dumps({"ts": t, "seq": seq, "head": head}) + "\n")       # same shape as Ledger.write_anchor: verify_anchor_file reads it as is
            f.flush(); os.fsync(f.fileno())
    except BaseException as e:
        for leftover in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm"), Path(str(final or tmp) + ".partial"), dest / (name + ".json"), dest / (name + ".json.partial")):
            leftover.unlink(missing_ok=True)
        if final is not None:
            final.unlink(missing_ok=True)
        if isinstance(e, OSError):
            raise BackupError(f"could not write the backup in {dest}: {e}") from e
        raise
    prune(dest, keep)
    return meta


def list_backups(dest) -> list[dict]:
    """newest first (by creation time, not by file name: a clock or timezone change must not reorder them); unreadable or odd sidecars are skipped"""
    out = []
    for js in Path(dest).glob("ledger-*.json"):
        try:
            m = json.loads(js.read_text())
            ok = (isinstance(m, dict) and isinstance(m.get("file"), str) and Path(m["file"]).name == m["file"] and m["file"].startswith("ledger-")
                  and isinstance(m.get("created"), (int, float)) and isinstance(m.get("sha256"), str) and isinstance(m.get("head"), str))
        except (OSError, ValueError):
            continue
        if not ok:
            continue
        m["present"] = (Path(dest) / m["file"]).exists()
        m.setdefault("stored_bytes", 0); m.setdefault("trials", 0); m.setdefault("events", 0); m.setdefault("seq", 0); m.setdefault("source", "")
        out.append(m)
    return sorted(out, key=lambda m: m["created"], reverse=True)


def _sidecar(file_name: str) -> str:
    return file_name.removesuffix(".gz").removesuffix(".sqlite") + ".json"


def prune(dest, keep: int) -> list[str]:
    """keep the newest `keep` backups; anchors.jsonl is never pruned (it is the part worth keeping forever, and tiny)"""
    gone = []
    for m in list_backups(dest)[max(keep, 1):]:                  # at least the newest one is always kept
        for n in (m.get("file", ""), _sidecar(m.get("file", ""))):
            if n and (Path(dest) / n).exists():
                (Path(dest) / n).unlink()
                gone.append(n)
    return gone


def verify_backup(dest, meta: dict) -> None:
    """raise BackupError unless the file matches its sidecar and is a healthy, chain-intact ledger"""
    f = Path(dest) / meta["file"]
    if not f.exists():
        raise BackupError(f"{f} is missing")
    tmp = None
    try:
        if meta.get("compressed"):
            tmp = f.with_suffix(".verify")
            with gzip.open(f, "rb") as fi, open(tmp, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            target = tmp
        else:
            target = f
        if _sha256(target) != meta["sha256"]:
            raise BackupError(f"{f.name}: sha256 does not match its sidecar; the file changed or is damaged")
        probe = Path(str(target) + ".probe")
        shutil.copyfile(target, probe)
        try:
            led = Ledger(probe)
            info = led.check(full=True)
            led.close()
        finally:
            for ext in ("", "-wal", "-shm"):
                Path(str(probe) + ext).unlink(missing_ok=True)
        if not info["integrity_ok"] or not info["chain_ok"]:
            raise BackupError(f"{f.name}: failed verification (integrity={info['integrity']}, chain_ok={info['chain_ok']})")
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def _current_seq(target: Path) -> int:
    try:
        c = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
        try:
            return c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        return 0                                                  # unreadable: nothing worth protecting


def _aside(target: Path) -> Path:
    stamp = time.strftime(STAMP) + f"-{os.getpid()}"
    for n in range(100):
        cand = target.with_name(target.name + f".replaced-{stamp}" + (f"-{n}" if n else ""))
        if not cand.exists():
            return cand
    raise BackupError("could not find a free name for the replaced ledger")


def restore_backup(dest, which: str | None, target, force: bool = False, lock_check=None, allow_different: bool = False) -> dict:
    """Put a verified backup in place of `target` (a ledger path). `which` is a file name or timestamp prefix; default: the newest.
    The old file (and any write-ahead log left beside it) is moved aside, never deleted. Refused unless `allow_different`: a snapshot
    taken from another ledger, or one that is behind the ledger it would replace (that would give back credits already spent and
    forget events already anchored). `lock_check()` should raise if a daemon is running on the workdir."""
    metas = list_backups(dest)
    if not metas:
        raise BackupError(f"no backups in {dest}")
    meta = next((m for m in metas if which is None or m["file"].startswith(which) or which in m["file"]), None)
    if meta is None:
        raise BackupError(f"no backup matches {which!r}; have: {', '.join(m['file'] for m in metas[:5])}")
    target = Path(target)
    if lock_check:
        lock_check()
    verify_backup(dest, meta)
    if target.exists() and not force:
        raise BackupError(f"{target} exists; pass --force to move it aside and restore over it")
    if not allow_different:
        if meta["source"] and target.exists() and Path(meta["source"]) != target.resolve():
            raise BackupError(f"{meta['file']} was taken from {meta['source']}, not from {target}; pass --allow-different if that is intended")
        cur = _current_seq(target) if target.exists() else 0
        if meta["seq"] < cur:
            raise BackupError(f"{meta['file']} stops at event {meta['seq']} but {target} is at event {cur}: restoring would forget {cur - meta['seq']} "
                              f"event(s) and give back spent sealed looks; pass --allow-different to do it anyway")
    target.parent.mkdir(parents=True, exist_ok=True)
    aside = None
    if target.exists() or Path(str(target) + "-wal").exists() or Path(str(target) + "-shm").exists():
        aside = _aside(target)
        for ext in ("", "-wal", "-shm"):
            if Path(str(target) + ext).exists():
                os.replace(str(target) + ext, str(aside) + ext)
    src = Path(dest) / meta["file"]
    tmp = target.with_name(target.name + ".restoring")
    if meta.get("compressed"):
        with gzip.open(src, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    else:
        shutil.copyfile(src, tmp)
    os.replace(tmp, target)
    return {"restored": meta["file"], "to": str(target), "moved_aside": str(aside) if aside else None, "seq": meta["seq"], "head": meta["head"]}
