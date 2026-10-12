"""Backups of the ledger that are consistent, verified, and restorable. Standard library only.

A backup is made with SQLite's online backup API, so the daemon keeps running and the copy is a consistent snapshot (never a torn
file copy of a database with a live WAL). It is only kept if it then passes SQLite's integrity check and the event-chain check,
and it is described by a small sidecar file (size, sha256, event count, chain head, schema version) so that a restore can be
verified before it replaces anything. The chain head is also appended to `anchors.jsonl`: copy that file, or the whole directory,
off the machine, because an anchor kept next to the ledger cannot expose a rewritten ledger.

    <dest>/ledger-20261013-031500.sqlite[.gz]     the snapshot
    <dest>/ledger-20261013-031500.json            sidecar: sha256, bytes, seq, head, schema_version, trials, created
    <dest>/anchors.jsonl                          one line per backup: {"ts", "seq", "head", "file"}
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


def make_backup(ledger_path, dest, keep: int = 14, compress: bool = False, now: float | None = None) -> dict:
    """Snapshot `ledger_path` into `dest`; returns the sidecar dict. Raises BackupError (and keeps nothing) if the copy does not verify."""
    ledger_path, dest = Path(ledger_path), Path(dest)
    if not ledger_path.exists():
        raise BackupError(f"no ledger at {ledger_path}")
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free
    need = ledger_path.stat().st_size * 2 + (64 << 20)
    if free < need:
        raise BackupError(f"only {free >> 20} MB free in {dest}; a backup of this ledger needs about {need >> 20} MB")
    t = time.time() if now is None else now
    name = "ledger-" + time.strftime(STAMP, time.localtime(t))
    tmp = dest / (name + ".sqlite.partial")
    src = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True, timeout=30)
    try:
        out = sqlite3.connect(tmp)
        try:
            src.backup(out)                                   # consistent online snapshot
        finally:
            out.close()
    finally:
        src.close()
    try:
        led = Ledger(tmp)                                     # opens read-write; the copy is ours and still private
        info = led.check(full=True)
        head = led.head()
        seq = led.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        led.checkpoint()
        led.close()
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"the snapshot could not be opened: {type(e).__name__}: {e}") from e
    if not info["integrity_ok"] or not info["chain_ok"]:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"the snapshot failed verification (integrity={info['integrity']}, chain_ok={info['chain_ok']}); nothing was kept")
    for ext in ("-wal", "-shm"):
        Path(str(tmp) + ext).unlink(missing_ok=True)
    final = dest / (name + ".sqlite")
    sha = _sha256(tmp)
    size = tmp.stat().st_size
    if compress:
        final = dest / (name + ".sqlite.gz")
        with open(tmp, "rb") as fi, gzip.open(str(final) + ".partial", "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        os.replace(str(final) + ".partial", final)
        tmp.unlink()
    else:
        os.replace(tmp, final)
    meta = {"file": final.name, "created": t, "sha256": sha, "bytes": size, "stored_bytes": final.stat().st_size, "compressed": compress,
            "schema_version": info["schema_version"], "trials": info["trials"], "events": info["events"], "seq": seq, "head": head,
            "source": str(ledger_path)}
    (dest / (name + ".json")).write_text(json.dumps(meta, indent=1))
    with open(dest / "anchors.jsonl", "a") as f:
        f.write(json.dumps({"ts": t, "seq": seq, "head": head, "file": final.name}) + "\n")
    prune(dest, keep)
    return meta


def list_backups(dest) -> list[dict]:
    out = []
    for js in sorted(Path(dest).glob("ledger-*.json"), reverse=True):
        try:
            m = json.loads(js.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        m["present"] = (Path(dest) / m.get("file", "")).exists()
        out.append(m)
    return out


def _sidecar(file_name: str) -> str:
    return file_name.removesuffix(".gz").removesuffix(".sqlite") + ".json"


def prune(dest, keep: int) -> list[str]:
    """keep the newest `keep` backups; anchors.jsonl is never pruned (it is the part worth keeping forever, and tiny)"""
    gone = []
    for m in list_backups(dest)[max(keep, 1):]:
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


def restore_backup(dest, which: str | None, target, force: bool = False, lock_check=None) -> dict:
    """Put a verified backup in place of `target` (a ledger path). `which` is a file name or timestamp prefix; default: the newest.
    The old file is moved aside, never deleted. `lock_check()` should raise if a daemon is running on the workdir."""
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
    target.parent.mkdir(parents=True, exist_ok=True)
    aside = None
    if target.exists():
        aside = target.with_name(target.name + f".replaced-{time.strftime(STAMP)}")
        os.replace(target, aside)
        for ext in ("-wal", "-shm"):
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
