"""Disk watchdog + safe cleanup.

An artifact may be deleted ONLY if every condition below holds (all checked at delete time):
  protected=0, kind not in NEVER_DELETE, archived & remote hash == recorded hash, verified_at set,
  older than hot_s, its trial is not queued/running, trial is not on the keep list (best per cap bin),
  file exists, and the file on disk still hashes to exactly what was verified remotely.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .ledger import Ledger, sha256_file

NEVER_DELETE = {"ledger", "manifest", "config", "model_best", "report"}
LEVELS = ("ok", "warn", "clean", "pause", "hardstop")


def free_gb(path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:        # workdir may not exist yet on first start
        p = p.parent
    return shutil.disk_usage(p).free / (1 << 30)


def disk_level(free: float, th: dict) -> str:
    if free < th["hardstop"]:
        return "hardstop"
    if free < th["pause"]:
        return "pause"
    if free < th["clean"]:
        return "clean"
    if free < th["warn"]:
        return "warn"
    return "ok"


def _eligible(a: dict, now: float, hot_s: float, keep: set, busy: set) -> bool:
    """The delete rule, evaluated on a FRESH artifact row both when planning and again at delete time.
    A verified_at in the future (clock stepped back) gives a negative age, i.e. 'hot': never deleted."""
    return (a["deleted_at"] is None and not a["protected"] and a["archived_uri"] is not None and a["verified_at"] is not None
            and a["kind"] not in NEVER_DELETE and a["archived_sha256"] == a["sha256"]
            and now - a["verified_at"] >= hot_s and a["trial_id"] not in keep and a["trial_id"] not in busy)


def plan_cleanup(ledger: Ledger, need_bytes: int, hot_s: float = 6 * 3600, keep_trials=(),
                 now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    keep = set(keep_trials)
    busy = {r[0] for r in ledger.conn.execute("SELECT id FROM trials WHERE state IN ('queued','running')")}
    plan, total = [], 0
    rows = ledger.conn.execute("SELECT * FROM artifacts WHERE deleted_at IS NULL AND protected=0 AND archived_uri IS NOT NULL "
                               "AND verified_at IS NOT NULL ORDER BY verified_at, id")      # streamed: the table can be huge
    for r in rows:
        a = dict(r)
        if not _eligible(a, now, hot_s, keep, busy):
            continue
        plan.append(a)
        if not os.path.lexists(a["path"]):       # already gone: execute_cleanup retires the row; it frees nothing, so it
            continue                             # must not count towards need_bytes (it used to fill the plan forever)
        total += a["bytes"]
        if total >= need_bytes:
            break
    return plan


def execute_cleanup(ledger: Ledger, plan: list[dict], dry_run: bool = False, hot_s: float = 6 * 3600, keep_trials=(),
                    now: float | None = None) -> dict:
    """Delete the planned files. Everything is checked again NOW, on the current ledger row: the plan may be minutes old and
    meanwhile a file can have been protected, re-registered (new bytes, verification reset), re-verified (hot again) or
    belong to a trial that was requeued."""
    now = time.time() if now is None else now
    keep = set(keep_trials)
    freed = deleted = skipped = missing = 0
    for planned in plan:
        a = ledger.artifact(planned["path"])
        if a is None:
            skipped += 1
            continue
        busy = {r[0] for r in ledger.conn.execute("SELECT id FROM trials WHERE id=? AND state IN ('queued','running')",
                                                  (a["trial_id"],))}
        if not _eligible(a, now, hot_s, keep, busy):
            skipped += 1
            continue
        p = Path(a["path"])
        if not os.path.lexists(p):                # unlinked by hand, or by us just before a crash: the verified remote copy exists
            if not dry_run:
                ledger.mark_deleted(a["path"], 0)
            missing += 1
            continue
        try:
            same = sha256_file(p) == a["sha256"]  # re-verify at delete time
        except OSError:                           # unreadable, or a directory now: leave it alone
            same = False
        if not same:
            skipped += 1
            continue
        if not dry_run:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            ledger.mark_deleted(a["path"], a["bytes"])
        freed += a["bytes"]; deleted += 1
    return {"deleted": deleted, "skipped": skipped, "missing": missing, "freed": freed, "dry_run": dry_run}
