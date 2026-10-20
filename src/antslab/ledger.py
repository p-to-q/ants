"""SQLite ledger: the single source of truth for trials, artifacts and evidence.

Design points
- Every state change and its audit event are written in ONE transaction.
- Events are hash-chained, so deleting/editing a middle row is detectable. The hash is unkeyed, so truncating the tail or
  rewriting history with recomputed hashes is only detectable against an EXTERNAL anchor: store `anchor()` off-box (see
  docs/evaluation/E7-claim.md) and call `verify_against_anchor`.
- `attempt` is a fencing token: a stale runner cannot overwrite a reclaimed trial.
- Stdlib only, so the 2 GB control tower carries no third-party dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS trials(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gym TEXT NOT NULL, arm TEXT NOT NULL, seed INTEGER NOT NULL,
  params TEXT NOT NULL, source TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 's0', spec_key TEXT NOT NULL DEFAULT '', parent_id INTEGER,
  origin_key TEXT, edit TEXT, epoch TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'queued',
  attempt INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0,
  created_at REAL, started_at REAL, finished_at REAL, lease_until REAL,
  metrics TEXT, error TEXT, out_dir TEXT, cpu_s REAL);
CREATE INDEX IF NOT EXISTS ix_trials_state ON trials(state, priority DESC, id);
CREATE INDEX IF NOT EXISTS ix_trials_arm ON trials(gym, arm, id);
CREATE INDEX IF NOT EXISTS ix_trials_spec ON trials(gym, arm, spec_key, stage);
CREATE INDEX IF NOT EXISTS ix_trials_fin ON trials(gym, arm, state, finished_at);
CREATE TABLE IF NOT EXISTS epochs(id TEXT PRIMARY KEY, ts REAL NOT NULL, info TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, seq);
CREATE TABLE IF NOT EXISTS artifacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, trial_id INTEGER, path TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
  protected INTEGER NOT NULL DEFAULT 0,
  archived_uri TEXT, archived_sha256 TEXT, verified_at REAL, deleted_at REAL);
CREATE TABLE IF NOT EXISTS unseals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, gym TEXT NOT NULL, ts REAL NOT NULL,
  candidate TEXT NOT NULL, result TEXT, epoch TEXT NOT NULL DEFAULT '', sealed_set TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS judgements(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, judge TEXT NOT NULL, kind TEXT NOT NULL,
  subject TEXT NOT NULL, p REAL, label TEXT, raw TEXT, authority TEXT NOT NULL, outcome INTEGER, resolved_at REAL);
CREATE INDEX IF NOT EXISTS ix_judge ON judgements(judge, kind, outcome);
CREATE TABLE IF NOT EXISTS llm_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, model TEXT,
  ok INTEGER NOT NULL, tokens INTEGER, note TEXT);
"""

# Schema version, kept in the file itself (PRAGMA user_version). A ledger is a long-lived record: opening one written by a newer
# ants is refused instead of half-understood; opening an older one runs the missing steps in order, after a safety copy when a
# step changes existing data. Add a step by bumping SCHEMA_VERSION and appending to MIGRATIONS (never edit an old step).
SCHEMA_VERSION = 2

FINAL_STATES = ("done", "failed", "timeout", "crashed")
# Event kinds that PROVE a state change (freeze, sealed look/result, model gates, promotion tests). They can only be written by the
# methods that enforce the rules; the public Ledger.event() refuses them, so no caller can forge a sealed result.
RESERVED_KINDS = frozenset({"candidate_frozen", "sealed_evaluation", "sealed_result", "model_gates", "model_status",
                            "model_registered", "alpha_wealth"})


class UnsealBudgetExceeded(RuntimeError):
    pass


class LedgerTooNew(RuntimeError):
    """The file was written by a newer version of ants than this one."""


class GateError(RuntimeError):
    pass


class Anchor(NamedTuple):
    """(seq, head hash, timestamp) of the event chain at one moment; keep copies where the ledger's writer cannot edit them."""
    seq: int
    head: str
    ts: float


def fs_type(path) -> str:
    """filesystem type of `path` (Linux: /proc/mounts), '' when unknown. SQLite's WAL mode is not safe on network filesystems."""
    try:
        best, kind = "", ""
        real = os.path.realpath(path)
        for line in Path("/proc/mounts").read_text().splitlines():
            dev, mnt, fst = line.split()[:3]
            if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
                best, kind = mnt, fst
        return kind
    except OSError:
        return ""


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        try:
            self._refuse_newer()                                             # before any pragma that would write to the file
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA journal_size_limit=67108864")          # the WAL file may not stay larger than 64 MB after a checkpoint
            self.conn.execute("PRAGMA wal_autocheckpoint=1000")
            self._upgrade()
        except BaseException:
            self.conn.close()
            raise

    @classmethod
    def open_readonly(cls, path) -> "Ledger":
        """A viewer's connection: `mode=ro` plus `query_only`, no schema creation, no migration, no pragma that writes. The live
        daemon's file is never touched, so looking at it cannot slow it down or change it (any method that writes raises)."""
        self = cls.__new__(cls)
        self.path = str(path)
        self._lock = threading.RLock()
        if not Path(self.path).exists():
            raise FileNotFoundError(f"no ledger at {self.path}")
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10, isolation_level=None, check_same_thread=False)
        try:
            self.conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.OperationalError as e:        # a directory we cannot write beside and no write-ahead log to read: the file is at rest
            self.conn.close()
            if "locked" in str(e):
                raise
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True, timeout=10, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA busy_timeout=10000")
            self.conn.execute("PRAGMA query_only=ON")
            self._refuse_newer()
        except BaseException:
            self.conn.close()
            raise
        return self

    def _refuse_newer(self):
        v = self.schema_version()
        if v > SCHEMA_VERSION:
            raise LedgerTooNew(f"{self.path} has schema version {v}; this ants understands up to {SCHEMA_VERSION}. Upgrade ants; the file was not touched.")

    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _upgrade(self):
        v = self.schema_version()
        self._refuse_newer()
        self.conn.executescript(SCHEMA)                                   # creates anything missing; never alters existing data
        if v >= SCHEMA_VERSION:
            return
        if self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
            self._safety_copy(v)                                           # BEFORE the write lock: copying a 250 MB ledger must not hold a running arm's writes (its busy_timeout is 30 s)
        self.conn.execute("BEGIN IMMEDIATE")                               # several processes may open an old file at once: one migrates, the rest re-check
        try:
            v = self.schema_version()
            for ver in range(v + 1, SCHEMA_VERSION + 1):
                MIGRATIONS[ver](self)
                self.conn.execute(f"PRAGMA user_version={ver}")
            self.conn.execute("COMMIT")
        except BaseException:
            self._abort()
            raise

    def _safety_copy(self, v: int):
        """a full copy next to the ledger before the first migration of its data (kept; delete it by hand once the new version has run)"""
        dest = Path(self.path + f".pre-migration-v{v}")
        if dest.exists():
            # Keep a copy that really is a ledger at schema version v (an earlier attempt that did not finish migrating, or the copy of an opener
            # that has since migrated: that one is the rollback copy). A restored ledger can have the same schema version but different contents,
            # so compare its event-chain endpoint as well. File times are not reliable: a live WAL ledger's main file moves at every checkpoint.
            try:
                chk = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
                try:
                    def identity(conn):
                        version = conn.execute("PRAGMA user_version").fetchone()[0]
                        count, seq, digest = conn.execute(
                            "SELECT COUNT(*), COALESCE(MAX(seq), 0), "
                            "(SELECT hash FROM events ORDER BY seq DESC LIMIT 1) FROM events"
                        ).fetchone()
                        return version, count, seq, digest

                    saved_identity = identity(chk)
                    source_identity = identity(self.conn)
                    # A concurrent opener can arrive here with a stale version read after another opener completed the migration. In that
                    # case the old-schema copy is the rollback copy even though the live ledger has since accumulated new events.
                    keep = saved_identity[0] == v and (source_identity[0] > v or saved_identity == source_identity)
                finally:
                    chk.close()
            except sqlite3.Error:
                keep = False
            if keep:
                return
            try:
                os.replace(dest, Path(f"{dest}.stale-{int(time.time())}"))
            except OSError:
                return
        part = Path(f"{dest}.partial.{os.getpid()}.{threading.get_ident()}")
        out = sqlite3.connect(part)
        try:
            self.conn.backup(out)
        except BaseException:
            out.close()
            part.unlink(missing_ok=True)
            raise
        out.close()
        os.replace(part, dest)

    def _m1_baseline(self):
        """version 1 is the layout every earlier ledger already has (plus the columns added in place before versions existed)"""
        self._migrate_unseals()

    def _migrate_unseals(self):
        """Ledgers created before sealed credits were scoped by (epoch, sealed set) lack those columns."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(unseals)")}
        for col in ("epoch", "sealed_set"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE unseals ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        try:      # database-level backstop: one look per (gym, epoch, sealed set, frozen candidate)
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_unseals_look ON unseals(gym, epoch, sealed_set, candidate)")
        except sqlite3.IntegrityError:
            pass      # legacy duplicate rows: record_unseal still refuses a second look in code

    def close(self):
        self.conn.close()

    def checkpoint(self, truncate: bool = True) -> tuple:
        """fold the WAL into the main file (and shrink it): (busy, log frames, checkpointed frames)"""
        return tuple(self.conn.execute(f"PRAGMA wal_checkpoint({'TRUNCATE' if truncate else 'PASSIVE'})").fetchone())

    def check(self, full: bool = False) -> dict:
        """durability facts for `ants doctor` and backups: file sizes, schema, SQLite's own integrity check, the event chain"""
        p = Path(self.path)
        wal = Path(self.path + "-wal")
        ic = [r[0] for r in self.conn.execute("PRAGMA integrity_check" if full else "PRAGMA quick_check")]
        ok_chain, bad = self.verify_chain()
        return {"path": self.path, "schema_version": self.schema_version(), "bytes": p.stat().st_size if p.exists() else 0,
                "wal_bytes": wal.stat().st_size if wal.exists() else 0, "integrity": ic[0] if ic == ["ok"] else ic[:5],
                "integrity_ok": ic == ["ok"], "chain_ok": ok_chain, "chain_bad_seq": bad,
                "trials": self.conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0],
                "events": self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "fs": fs_type(self.path)}

    def _abort(self):
        """Leave no transaction open. COMMIT can fail ('database is locked', I/O error) with the transaction still active, and
        SQLite sometimes rolls back by itself (disk full): in both cases the NEXT `BEGIN` must work, and the original error
        must reach the caller (so this never raises)."""
        try:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @contextmanager
    def tx(self):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self._abort()
                raise

    # ---- events -------------------------------------------------------
    @staticmethod
    def _hash(prev: str, ts: float, kind: str, body: str) -> str:
        return sha256_text(f"{prev}|{ts!r}|{kind}|{body}")

    def _stamp(self, c) -> float:
        """Wall time, but never earlier than the newest event: a clock stepped backwards (NTP, a VM booted with a wrong time)
        must not reorder history. History tails `finished_at` and the circuit breaker reads the latest finishes by it."""
        row = c.execute("SELECT ts FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        now = time.time()
        return max(now, row[0]) if row else now

    def _emit(self, c, kind: str, payload: dict, ts: float | None = None):
        row = c.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        ts = self._stamp(c) if ts is None else ts
        body = canon(payload)
        c.execute("INSERT INTO events(ts,kind,payload,prev_hash,hash) VALUES(?,?,?,?,?)",
                  (ts, kind, body, prev, self._hash(prev, ts, kind, body)))

    def event(self, kind: str, payload: dict):
        if kind in RESERVED_KINDS:
            raise GateError(f"event kind {kind!r} is reserved for the gate / registry and cannot be written directly")
        with self.tx() as c:
            self._emit(c, kind, payload)

    def head(self) -> str:
        row = self.conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else GENESIS

    def verify_chain(self, upto: int | None = None) -> tuple[bool, int | None]:
        """Walk the chain from the genesis: links, hashes and contiguous sequence numbers (events are never deleted
        legitimately, so a gap means a deleted row followed by new writes). Returns (ok, first bad seq)."""
        prev, expect = GENESIS, 1
        for r in self.conn.execute("SELECT * FROM events ORDER BY seq" + (" LIMIT ?" if upto else ""), (upto,) if upto else ()):
            if r["seq"] != expect or r["prev_hash"] != prev or r["hash"] != self._hash(
                    r["prev_hash"], r["ts"], r["kind"], r["payload"]):
                return False, r["seq"]
            prev, expect = r["hash"], expect + 1
        return True, None

    def recent_events(self, kind: str, n: int = 5) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM events WHERE kind=? ORDER BY seq DESC LIMIT ?", (kind, n)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in reversed(rows)]

    def verify_tail(self, n: int = 2000) -> tuple[bool, int | None]:
        """Cheap integrity check for the last n events (full verify_chain is O(all events)). The window includes one extra
        predecessor so the first checked row is linked to what precedes it. Does NOT detect truncation of the tail
        or a consistent rewrite: that needs an anchor (verify_against_anchor)."""
        rows = self.conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (n + 1,)).fetchall()[::-1]
        if len(rows) <= n and rows and rows[0]["prev_hash"] != GENESIS:      # the window reaches the genesis
            return False, rows[0]["seq"]
        for prev, r in zip(rows, rows[1:]):
            if r["prev_hash"] != prev["hash"] or r["seq"] != prev["seq"] + 1:
                return False, r["seq"]
        for r in rows:
            if r["hash"] != self._hash(r["prev_hash"], r["ts"], r["kind"], r["payload"]):
                return False, r["seq"]
        return True, None

    # ---- external anchor ------------------------------------------------
    def anchor(self) -> Anchor:
        """Snapshot (seq, head hash, now) of the chain. Store it OFF-BOX (an append-only place the ledger's writer cannot
        edit) at least at every freeze and after every sealed evaluation; later `verify_against_anchor` detects tail
        truncation and any rewrite of history up to that point, even one with consistently recomputed hashes."""
        row = self.conn.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return Anchor(row["seq"], row["hash"], time.time()) if row else Anchor(0, GENESIS, time.time())

    def verify_against_anchor(self, anchor) -> tuple[bool, str | None]:
        """(ok, reason). The chain must be internally intact AND still contain, at anchor.seq, exactly the anchored head."""
        a = Anchor(*anchor)
        if a.seq == 0:
            return (a.head == GENESIS), (None if a.head == GENESIS else "anchor claims an empty chain with a non-genesis head")
        row = self.conn.execute("SELECT hash FROM events WHERE seq=?", (a.seq,)).fetchone()
        if row is None:
            last = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            return False, f"truncated: anchor at seq {a.seq} but the chain ends at seq {last}"
        if row["hash"] != a.head:
            return False, f"rewritten: event {a.seq} no longer has the anchored hash"
        ok, bad = self.verify_chain(upto=a.seq)
        return (True, None) if ok else (False, f"chain broken at seq {bad}")

    def write_anchor(self, path) -> Anchor:
        """Append the current anchor as one JSON line to `path` (ship that file off-box, e.g. mail / git remote / object lock)."""
        a = self.anchor()
        with open(path, "a") as f:
            f.write(json.dumps(a._asdict()) + "\n")
            f.flush(); os.fsync(f.fileno())
        return a

    def verify_anchor_file(self, path) -> tuple[bool, str | None]:
        """Check every anchor line of a file written by write_anchor against this ledger."""
        for i, line in enumerate(Path(path).read_text().splitlines(), 1):
            if line.strip():
                d = json.loads(line)
                ok, why = self.verify_against_anchor(Anchor(**{k: d[k] for k in Anchor._fields}))      # extra keys (e.g. `file` from backups) are fine
                if not ok:
                    return False, f"anchor line {i}: {why}"
        return True, None

    def events(self, kind: str | None = None) -> list[dict]:
        q = "SELECT * FROM events" + (" WHERE kind=?" if kind else "") + " ORDER BY seq"
        rows = self.conn.execute(q, (kind,) if kind else ())
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    # ---- trials -------------------------------------------------------
    def enqueue(self, gym, arm, seed, params, source, priority=0, stage="s0",
                parent_id=None, spec_key=None, origin_key=None, edit=None, epoch="") -> int:
        """origin_key/edit record lineage: which spec this one was derived from and which levers changed."""
        key = spec_key or sha256_text(canon(params))[:16]
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO trials(gym,arm,seed,params,source,priority,created_at,stage,spec_key,parent_id,"
                "origin_key,edit,epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gym, arm, int(seed), canon(params), source, priority, time.time(), stage, key,
                 parent_id, origin_key, canon(edit) if edit is not None else None, epoch))
            return cur.lastrowid       # queued/running are transient: the finished event carries the full record

    # ---- O(1)/O(log n) aggregates for the hot loop (never scan all trials per step) ----
    def count_final(self, gym, arm) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM trials WHERE gym=? AND arm=? AND state IN ('done','failed','timeout','crashed')",
                                 (gym, arm)).fetchone()[0]

    def recent_final(self, gym, arm, n: int) -> list:
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT state, finished_at FROM trials WHERE gym=? AND arm=? AND state IN ('done','failed','timeout','crashed') "
            "ORDER BY finished_at DESC, id DESC LIMIT ?", (gym, arm, n))]

    def register_epoch(self, eid: str, info: dict) -> bool:
        """An epoch = one (evaluator code, contract, data) identity. Returns True if it was new."""
        with self.tx() as c:
            if c.execute("SELECT 1 FROM epochs WHERE id=?", (eid,)).fetchone():
                return False
            c.execute("INSERT INTO epochs(id,ts,info) VALUES(?,?,?)", (eid, time.time(), canon(info)))
            self._emit(c, "epoch_started", {"epoch": eid, **info})
            return True

    def seen_keys(self, gym, arm, epoch: str | None = None) -> set:
        """Specs already measured; with `epoch`, only those measured by that identity (a new epoch re-measures old specs)."""
        q, args = "SELECT DISTINCT spec_key FROM trials WHERE gym=? AND arm=?", [gym, arm]
        if epoch is not None:
            q, args = q + " AND epoch=?", args + [epoch]
        return {r[0] for r in self.conn.execute(q, args)}

    def cpu_total(self, gym, arm) -> float:
        return self.conn.execute("SELECT COALESCE(SUM(cpu_s),0) FROM trials WHERE gym=? AND arm=?",
                                 (gym, arm)).fetchone()[0]

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["metrics"] = json.loads(d["metrics"]) if d["metrics"] else None
        d["edit"] = json.loads(d["edit"]) if d.get("edit") else None
        return d

    def get(self, tid: int) -> dict:
        return self._row(self.conn.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone())

    def claim(self, lease_s: float, gym=None, arm=None, now=None, epoch=None) -> dict | None:
        """epoch: the identity of the measurement machinery that will RUN the trial. It is stamped here, not at enqueue: a trial
        queued before an evaluator/data change and executed after it belongs to the epoch that measured it."""
        now = time.time() if now is None else now
        where, args = "state='queued'", []
        if gym:
            where += " AND gym=?"; args.append(gym)
        if arm:
            where += " AND arm=?"; args.append(arm)
        with self.tx() as c:
            r = c.execute(f"SELECT id FROM trials WHERE {where} ORDER BY priority DESC,id LIMIT 1",
                          args).fetchone()
            if not r:
                return None
            c.execute("UPDATE trials SET state='running',attempt=attempt+1,started_at=?,"
                      "lease_until=?,epoch=COALESCE(?,epoch) WHERE id=?", (now, now + lease_s, epoch, r["id"]))
            return self._row(c.execute("SELECT * FROM trials WHERE id=?", (r["id"],)).fetchone())

    def finish(self, tid, attempt, state, metrics=None, error=None, cpu_s=None, out_dir=None) -> bool:
        assert state in FINAL_STATES
        with self.tx() as c:
            ts = self._stamp(c)
            cur = c.execute(
                "UPDATE trials SET state=?,metrics=?,error=?,cpu_s=?,out_dir=?,finished_at=?,"
                "lease_until=NULL WHERE id=? AND attempt=? AND state='running'",
                (state, canon(metrics) if metrics is not None else None, error, cpu_s,
                 out_dir, ts, tid, attempt))
            if cur.rowcount != 1:
                return False
            r = c.execute("SELECT arm,stage,seed,params,source,spec_key,origin_key,edit,parent_id,epoch FROM trials WHERE id=?",
                          (tid,)).fetchone()
            self._emit(c, "trial_finished", {"id": tid, "attempt": attempt, "state": state, "arm": r["arm"],
                                             "stage": r["stage"], "seed": r["seed"], "params": json.loads(r["params"]),
                                             "source": r["source"], "spec_key": r["spec_key"],
                                             "origin_key": r["origin_key"], "edit": json.loads(r["edit"]) if r["edit"] else None,
                                             "parent_id": r["parent_id"], "epoch": r["epoch"], "cpu_s": cpu_s,
                                             "metrics": metrics, "error": error}, ts=ts)
            return True

    def requeue(self, tid, attempt, reason) -> bool:
        with self.tx() as c:
            cur = c.execute("UPDATE trials SET state='queued',lease_until=NULL WHERE id=? "
                            "AND attempt=? AND state='running'", (tid, attempt))
            if cur.rowcount != 1:
                return False
            self._emit(c, "trial_requeued", {"id": tid, "attempt": attempt, "reason": reason})
            return True

    def reclaim_stale(self, max_attempts: int, now=None, all_running=False) -> list[int]:
        """Recover trials whose runner died. Single-worker start-up passes all_running=True."""
        now = time.time() if now is None else now
        out = []
        with self.tx() as c:
            rows = c.execute("SELECT id,attempt,lease_until FROM trials WHERE state='running'").fetchall()
            for r in rows:
                if not all_running and r["lease_until"] is not None and r["lease_until"] > now:
                    continue
                if r["attempt"] >= max_attempts:
                    c.execute("UPDATE trials SET state='crashed',error='runner lost',finished_at=?,"
                              "lease_until=NULL WHERE id=?", (self._stamp(c), r["id"]))
                    self._emit(c, "trial_crashed", {"id": r["id"], "attempt": r["attempt"]})
                else:
                    c.execute("UPDATE trials SET state='queued',lease_until=NULL WHERE id=?", (r["id"],))
                    self._emit(c, "trial_reclaimed", {"id": r["id"], "attempt": r["attempt"]})
                out.append(r["id"])
        return out

    def trials(self, gym=None, arm=None, states=None) -> list[dict]:
        q, a = "SELECT * FROM trials WHERE 1=1", []
        if gym:
            q += " AND gym=?"; a.append(gym)
        if arm:
            q += " AND arm=?"; a.append(arm)
        if states:
            q += f" AND state IN ({','.join('?' * len(states))})"; a += list(states)
        return [self._row(r) for r in self.conn.execute(q + " ORDER BY id", a)]

    def counts(self, gym=None, arm=None) -> dict:
        out = {}
        for t in self.trials(gym, arm):
            out[t["state"]] = out.get(t["state"], 0) + 1
        return out

    # ---- artifacts ----------------------------------------------------
    def add_artifact(self, trial_id, path, kind, protected=False) -> dict:
        p = Path(path)
        sha, size = sha256_file(p), p.stat().st_size
        with self.tx() as c:
            c.execute("INSERT INTO artifacts(trial_id,path,kind,sha256,bytes,protected) VALUES(?,?,?,?,?,?)"
                      " ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,bytes=excluded.bytes,"
                      "archived_uri=NULL,archived_sha256=NULL,verified_at=NULL,deleted_at=NULL,"
                      "protected=MAX(protected,excluded.protected)",
                      (trial_id, str(p), kind, sha, size, int(protected)))
            self._emit(c, "artifact_added", {"path": str(p), "kind": kind, "sha256": sha,
                                             "bytes": size, "trial_id": trial_id})
        return self.artifact(str(p))

    def artifact(self, path: str):
        r = self.conn.execute("SELECT * FROM artifacts WHERE path=?", (path,)).fetchone()
        return dict(r) if r else None

    def artifacts(self, where="1=1", args=(), order="id", limit=None) -> list[dict]:
        q = f"SELECT * FROM artifacts WHERE {where} ORDER BY {order}" + (f" LIMIT {int(limit)}" if limit else "")
        return [dict(r) for r in self.conn.execute(q, args)]

    def mark_archived(self, path, uri, remote_sha) -> bool:
        with self.tx() as c:
            a = c.execute("SELECT sha256 FROM artifacts WHERE path=?", (path,)).fetchone()
            ok = bool(a) and a["sha256"] == remote_sha
            c.execute("UPDATE artifacts SET archived_uri=?,archived_sha256=?,verified_at=? WHERE path=?",
                      (uri, remote_sha, time.time() if ok else None, path))
            self._emit(c, "artifact_archived" if ok else "archive_mismatch",
                       {"path": path, "uri": uri, "remote_sha256": remote_sha})
            return ok

    def mark_deleted(self, path, freed: int):
        with self.tx() as c:
            c.execute("UPDATE artifacts SET deleted_at=? WHERE path=?", (time.time(), path))
            self._emit(c, "artifact_deleted", {"path": path, "freed": freed})

    def set_protected(self, path, value=True):
        with self.tx() as c:
            c.execute("UPDATE artifacts SET protected=? WHERE path=?", (int(value), path))

    # ---- evidence gate ------------------------------------------------
    # Sealed-credit rule (E7): looks are counted per (gym, epoch, sealed set) against the contract's `unseal_limit`, and per
    # (gym, sealed set) over all epochs against `unseal_lifetime`. One FROZEN CANDIDATE gets at most one look (a second look at
    # the same candidate is only a re-roll). The count is taken from the hash-chained events as well as from the table, so
    # deleting `unseals` rows does not refund credits. Everything happens in one BEGIN IMMEDIATE transaction (race-free
    # across processes).
    def freeze(self, gym, candidate: dict, fingerprint: str, evidence: dict, epoch: str | None = None,
               protocol: str = "") -> str:
        """Commit step: candidate + code fingerprint (+ epoch, protocol) are logged BEFORE any sealed evaluation.
        If the evidence names a trial, that trial must exist, belong to `gym`, have exactly this spec, and have been measured
        by evaluator code that hashes to `fingerprint` (epoch of the evidence = epoch of the freeze): evidence from an earlier
        epoch cannot be frozen under today's code. Idempotent: freezing the same thing again returns the same hash."""
        tid = (evidence or {}).get("trial_id")
        if tid is not None:
            r = self.conn.execute("SELECT gym,params,epoch FROM trials WHERE id=?", (tid,)).fetchone()
            if r is None or r["gym"] != gym:
                raise GateError(f"freeze: evidence trial {tid} does not exist for gym {gym}")
            if json.loads(r["params"]) != candidate:
                raise GateError(f"freeze: candidate differs from the spec measured by evidence trial {tid}")
            if epoch is not None and epoch != r["epoch"]:
                raise GateError(f"freeze: evidence trial {tid} is from epoch {r['epoch']!r}, not {epoch!r}")
            epoch = r["epoch"]
        epoch = epoch or ""
        if epoch:
            e = self.conn.execute("SELECT info FROM epochs WHERE id=?", (epoch,)).fetchone()
            if e is not None and json.loads(e["info"]).get("code") not in (None, fingerprint[:16]):
                raise GateError(f"freeze: epoch {epoch} was measured with different evaluator code than the one being frozen")
        ident = {"gym": gym, "candidate": candidate, "code": fingerprint}
        if epoch:
            ident["epoch"] = epoch
        if protocol:
            ident["protocol"] = protocol
        h = sha256_text(canon(ident))
        if not self.is_frozen(gym, h):
            with self.tx() as c:
                self._emit(c, "candidate_frozen", {"gym": gym, "candidate": candidate, "code": fingerprint, "freeze_hash": h,
                                                   "evidence": evidence, "epoch": epoch, "protocol": protocol})
        return h

    def frozen(self, gym, freeze_hash) -> dict | None:
        """The payload of the candidate_frozen event with this hash (None if the candidate was never frozen)."""
        for e in self.events("candidate_frozen"):
            if e["payload"].get("freeze_hash") == freeze_hash and e["payload"].get("gym") == gym:
                return e["payload"]
        return None

    def is_frozen(self, gym, freeze_hash) -> bool:
        return self.frozen(gym, freeze_hash) is not None

    def _looks(self, c, gym, epoch=None, sealed_set=None, freeze_hash=None) -> int:
        """Looks spent, as the MAX of the mutable table and the hash-chained events (deleting table rows refunds nothing)."""
        q, a = "FROM unseals WHERE gym=?", [gym]
        for col, v in (("epoch", epoch), ("sealed_set", sealed_set), ("candidate", freeze_hash)):
            if v is not None:
                q += f" AND {col}=?"; a.append(v)
        n_tab = c.execute("SELECT COUNT(*) " + q, a).fetchone()[0]
        n_ev = 0
        for row in c.execute("SELECT payload FROM events WHERE kind='sealed_evaluation'"):
            p = json.loads(row[0])
            if p.get("gym") == gym and (epoch is None or p.get("epoch", "") == epoch) \
                    and (sealed_set is None or p.get("sealed_set", "") == sealed_set) \
                    and (freeze_hash is None or p.get("freeze_hash") == freeze_hash):
                n_ev += 1
        return max(n_tab, n_ev)

    def record_unseal(self, gym, freeze_hash, limit: int, result: dict, sealed_set: str = "",
                      lifetime: int | None = None) -> int:
        """Spend one sealed look BEFORE the evaluation runs. Raises GateError if the candidate was never frozen and
        UnsealBudgetExceeded if (a) this freeze hash already had its look, regardless of sealed set or epoch,
        (b) `limit` looks are spent for the
        candidate's (gym, epoch, sealed_set), or (c) `lifetime` looks are spent for (gym, sealed_set) over all epochs.
        The limits ratchet: a later call may lower `limit` / `lifetime` for a sealed set but can never raise what an earlier
        recorded look already used, so editing the contract after the fact does not buy looks.
        Returns the look number within (gym, epoch, sealed_set)."""
        with self.tx() as c:
            f = None
            for row in c.execute("SELECT payload FROM events WHERE kind='candidate_frozen'"):
                p = json.loads(row[0])
                if p.get("freeze_hash") == freeze_hash and p.get("gym") == gym:
                    f = p
                    break
            if f is None:
                raise GateError("candidate was never frozen; refusing to spend a sealed look")
            epoch = f.get("epoch", "")
            if self._looks(c, gym, freeze_hash=freeze_hash) >= 1:
                raise UnsealBudgetExceeded(f"{gym}: this frozen candidate already had its sealed look")
            prior = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='sealed_evaluation'")]
            prior = [p for p in prior if p.get("gym") == gym and p.get("sealed_set", "") == sealed_set]
            limit = min([limit] + [int(p["limit"]) for p in prior if p.get("limit") is not None])           # ratchet: limits only fall
            if lifetime is not None or any(p.get("lifetime") is not None for p in prior):
                lifetime = min([x for x in [lifetime] + [p.get("lifetime") for p in prior] if x is not None])
            n = self._looks(c, gym, epoch, sealed_set)
            if n >= limit:
                raise UnsealBudgetExceeded(f"{gym}: {n}/{limit} sealed evaluations already spent (epoch {epoch or '-'}, set {sealed_set or '-'})")
            life = self._looks(c, gym, None, sealed_set)
            if lifetime is not None and life >= lifetime:
                raise UnsealBudgetExceeded(f"{gym}: {life}/{lifetime} lifetime looks at sealed set {sealed_set or '-'} spent over all epochs")
            c.execute("INSERT INTO unseals(gym,ts,candidate,result,epoch,sealed_set) VALUES(?,?,?,?,?,?)",
                      (gym, time.time(), freeze_hash, canon(result), epoch, sealed_set))
            self._emit(c, "sealed_evaluation", {"gym": gym, "freeze_hash": freeze_hash, "epoch": epoch, "sealed_set": sealed_set,
                                                "result": result, "n": n + 1, "limit": limit, "lifetime": lifetime})
            return n + 1

    def unseal_count(self, gym) -> int:
        return self._looks(self.conn, gym)

    def record_sealed_result(self, gym, freeze_hash, n: int, metrics: dict | None, error: str | None, frozen_val=None):
        """Close a look: exactly one result per spent look. A result without a prior look (forgery), or a second result for the
        same look, is refused. The registry only trusts results written here."""
        with self.tx() as c:
            looks = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='sealed_evaluation'")]
            if not any(p.get("gym") == gym and p.get("freeze_hash") == freeze_hash and p.get("n") == n for p in looks):
                raise GateError("sealed result without a recorded look")
            for r in c.execute("SELECT payload FROM events WHERE kind='sealed_result'"):
                p = json.loads(r[0])
                if p.get("gym") == gym and p.get("freeze_hash") == freeze_hash and p.get("n") == n:
                    raise GateError("this sealed look already has a result")
            self._emit(c, "sealed_result", {"gym": gym, "freeze_hash": freeze_hash, "n": n, "metrics": metrics,
                                            "error": error, "frozen_val": frozen_val})

    def sealed_result(self, gym, freeze_hash) -> dict | None:
        """The successful sealed result of a frozen candidate (payload of its sealed_result event), else None."""
        looked = any(e["payload"].get("freeze_hash") == freeze_hash and e["payload"].get("gym") == gym
                     for e in self.events("sealed_evaluation"))
        if not looked:
            return None
        for e in reversed(self.events("sealed_result")):
            p = e["payload"]
            if p.get("freeze_hash") == freeze_hash and p.get("gym") == gym and p.get("metrics") and not p.get("error"):
                return p
        return None

    # ---- judge predictions (scored against realised outcomes) -----------
    def log_judgement(self, judge, kind, subject, p, label, raw, authority) -> int:
        with self.tx() as c:
            cur = c.execute("INSERT INTO judgements(ts,judge,kind,subject,p,label,raw,authority) VALUES(?,?,?,?,?,?,?,?)",
                            (time.time(), judge, kind, subject, p, label, (raw or "")[:4000], authority))
            return cur.lastrowid

    def resolve_judgement(self, jid, outcome: bool):
        with self.tx() as c:
            c.execute("UPDATE judgements SET outcome=?, resolved_at=? WHERE id=? AND outcome IS NULL",
                      (int(bool(outcome)), time.time(), jid))

    def judgements(self, judge, kind, resolved=None, limit=None) -> list[dict]:
        q = "SELECT * FROM judgements WHERE judge=? AND kind=?"
        if resolved is True:
            q += " AND outcome IS NOT NULL"
        elif resolved is False:
            q += " AND outcome IS NULL"
        q += " ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return [dict(r) for r in self.conn.execute(q, (judge, kind))]

    # ---- LLM budget ---------------------------------------------------
    def log_llm(self, model, ok, tokens=None, note=None, call_id: int | None = None,
                attempt: int | None = None, arm: str | None = None):
        with self.tx() as c:
            c.execute("INSERT INTO llm_calls(ts,model,ok,tokens,note,call_id,attempt,arm) VALUES(?,?,?,?,?,?,?,?)",
                      (time.time(), model, int(ok), tokens, note, call_id, attempt, arm))

    def llm_calls_since(self, since: float, ok_only=False, arm: str | None = None, unlabelled_only: bool = False) -> int:
        q = "SELECT COUNT(*) FROM llm_calls WHERE ts>=?"
        args: list = [since]
        if ok_only:
            q += " AND ok=1"
        if arm is not None:
            q += " AND arm=?"
            args.append(arm)
        if unlabelled_only:                                   # rows from before the `arm` column (schema v1)
            q += " AND arm IS NULL"
        return self.conn.execute(q, args).fetchone()[0]

    def llm_summary(self, since: float) -> dict:
        """Summary of model calls since `since`: per-endpoint and per-arm counts and failure causes.
        Returns {'endpoints': {name: {...}}, 'arms': {name: {...}}, 'totals': {...}}."""
        rows = self.conn.execute(
            "SELECT model, arm, ok, note, call_id, attempt FROM llm_calls WHERE ts>=?", (since,)).fetchall()
        endpoints: dict = {}
        arms: dict = {}
        total_attempts = 0
        logical_ids: set = set()
        ok_logical: set = set()
        for r in rows:
            model_str = r[0] or ""
            arm_val = r[1] or ""
            ok_val = r[2]
            note = r[3] or ""
            cid = r[4]
            total_attempts += 1

            ep_name = model_str.split(":")[0] if ":" in model_str else model_str
            if cid is not None:
                logical_ids.add((ep_name, cid))
                if ok_val:
                    ok_logical.add((ep_name, cid))

            cause = ""
            if not ok_val and note:
                cause = self._classify_note(note)

            for bucket, key in ((endpoints, ep_name), (arms, arm_val)):
                if key not in bucket:
                    bucket[key] = {"attempts": 0, "ok": 0, "causes": {}}
                bucket[key]["attempts"] += 1
                bucket[key]["ok"] += ok_val
                if cause:
                    bucket[key]["causes"][cause] = bucket[key]["causes"].get(cause, 0) + 1

        return {"endpoints": endpoints, "arms": arms,
                "totals": {"attempts": total_attempts, "logical_calls": len(logical_ids),
                           "ok_logical": len(ok_logical)}}

    @staticmethod
    def _classify_note(note: str) -> str:
        if note == "no-json":
            return "no_json"
        if "429" in note:
            return "http_429"
        if any(f"{c}" in note for c in range(500, 600)):
            return "http_5xx"
        low = note.lower()
        if "timeout" in low or "timed out" in low:
            return "timeout"
        if "reset" in low or "disconnect" in low or "connection" in low:
            return "connection"
        return "other"

    # ---- migration v2: llm_calls gets call_id, attempt, arm ----------------
    def _m2_llm_call_tracking(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(llm_calls)")}
        for col, typ in (("call_id", "INTEGER"), ("attempt", "INTEGER"), ("arm", "TEXT")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE llm_calls ADD COLUMN {col} {typ}")


MIGRATIONS = {1: Ledger._m1_baseline, 2: Ledger._m2_llm_call_tracking}
