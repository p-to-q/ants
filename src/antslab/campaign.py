"""The research loop: propose -> cascade-evaluate -> promote -> record, with all safety gates.

One call to step() does at most one evaluation. The caller (CLI / daemon / tests) owns sleeping,
so every decision here is deterministic and testable.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import platform
import random
import sqlite3
import sys
import time
from pathlib import Path

from .archive import compress_logs
from .contract import Contract, code_fingerprint, load_static_cost
from .janitor import disk_level, free_gb
import shutil

from .history import History
from .ledger import FINAL_STATES, GateError, Ledger
from .notify import heartbeat, keepalive, sd_notify
from .proposers import rank_specs
from .runner import run_command

DEFAULT_BATCH = 4              # proposals per refill; used by arm_definition hash and the Campaign default
BAD = ("failed", "timeout", "crashed")
_TRANSIENT_SQL = ("locked", "busy", "disk is full", "disk i/o", "unable to open database", "readonly database", "out of memory")
_TRANSIENT_ERRNO = {errno.ENOSPC, errno.EDQUOT, errno.EIO, errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN}
MAX_UNEXPECTED = 5          # consecutive errors that are neither transient SQLite nor OS resource errors: then exit non-zero


class Shutdown(BaseException):
    """Raised by the CLI's signal handler while the loop is blocked in a network call (no trial in flight)."""


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    """flock(2) on <workdir>/ants.lock. The kernel drops it when the process dies (even kill -9), and the fd is not inherited
    by gym children (close-on-exec), so a crashed daemon never blocks its successor. Two daemons on one workdir would each
    `recover()` (= requeue) the other's live trial and evaluate it twice."""

    def __init__(self, workdir):
        self.path, self.fd = Path(workdir) / "ants.lock", None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.pread(fd, 32, 0).decode("ascii", "replace").strip() or "unknown"
            os.close(fd)
            raise AlreadyRunning(f"another `ants run` (pid {holder}) already holds {self.path}; refusing to start a second "
                                 f"instance on the same workdir") from None
        except BaseException:
            os.close(fd)
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self.fd = fd

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def is_transient(e: BaseException) -> bool:
    """Errors that say 'not now' rather than 'broken': a busy / full / momentarily failing disk or a fork that cannot get memory."""
    if isinstance(e, sqlite3.OperationalError):
        m = str(e).lower()
        return any(t in m for t in _TRANSIENT_SQL)
    return isinstance(e, OSError) and e.errno in _TRANSIENT_ERRNO


class Campaign:
    promoter = None            # the arm's own rule for the first promotion cut (screen.py); None = the contract's quantile rule

    def __init__(self, ledger: Ledger, c: Contract, workdir, arm: str, proposer, seed: int = 0,
                 budget_evals: int | None = None, budget_cpu_s: float | None = None,
                 python: str | None = None, batch: int = DEFAULT_BATCH, breaker_n: int = 3, breaker_cooldown_s: float = 3600,
                 free_fn=None, dream_every: int = 0, dream_fn=None):
        self.l, self.c, self.arm, self.p, self.seed = ledger, c, arm, proposer, seed
        self.workdir = Path(workdir).resolve()       # gyms run with cwd=<out dir>: every path handed to them must be absolute
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.python = python or sys.executable
        self.budget_evals, self.budget_cpu_s, self.batch = budget_evals, budget_cpu_s, batch
        self.breaker_n, self.breaker_cooldown_s = breaker_n, breaker_cooldown_s
        self.free_fn = free_fn or (lambda: free_gb(self.workdir))
        self.dream_every, self.dream_fn = dream_every, dream_fn
        self.n_proposed = ledger.conn.execute("SELECT COUNT(*) FROM trials WHERE gym=? AND arm=?", (c.gym, arm)).fetchone()[0]
        self.promoter = getattr(proposer, "promoter", None)    # None = the contract's quantile rule decides every promotion
        self.epoch = self._epoch()
        ledger.register_epoch(self.epoch, self._epoch_info())
        self.hist = History(ledger, c, arm, epoch=self.epoch)
        self.static = load_static_cost(c.static_cost)
        self.static_mem = load_static_cost(c.static_mem)
        if hasattr(self.p, "accept"):                      # random / TPE: sample the feasible region instead of being starved by the filters
            self.p.accept = self._feasible
        self.rng = random.Random(seed * 7919 + 13)
        self.stats = {"static_rejected": 0, "stones_kept": 0, "db_errors": 0, "os_errors": 0, "step_errors": 0}
        self.required = [c.primary] + ([c.cap_metric] if c.cap_metric else [])
        self.interruptible = False              # True only while blocked in a network call with no trial in flight
        self._stop, self._sleep = (lambda: False), time.sleep
        self._brk = None                        # (final count, monotonic time the cooldown started)
        self._needs_reclaim, self._last_err_event = False, -1e9

    # ---- evidence identity ------------------------------------------------
    @staticmethod
    def _pkg_version(name: str):
        """Installed version by reading dist-info directory names (importlib.metadata costs ~0.3 s per package)."""
        for base in sys.path:
            try:
                for entry in os.listdir(base or "."):
                    if entry.lower().startswith(name.lower() + "-") and entry.endswith(".dist-info"):
                        return entry[len(name) + 1:-len(".dist-info")]
            except OSError:
                continue
        return None

    def _epoch_info(self) -> dict:
        """Identity of the measurement machinery: evaluator code, contract, data manifest. Versions are recorded for provenance
        but are NOT part of the identity hash (a numpy patch release must not silently fork the history)."""
        if getattr(self, "_info", None) is None:
            self._info = {"code": code_fingerprint(self.c.code_paths)[:16], "python": platform.python_version(),
                          "platform": platform.platform(), "numpy": self._pkg_version("numpy"), "scipy": self._pkg_version("scipy"),
                          "contract": hashlib.sha256(json.dumps(self.c.raw, sort_keys=True, default=str).encode()).hexdigest()[:16],
                          "data_manifest": self._data_hash()}
        return self._info

    def _data_hash(self) -> str:
        for cand in (Path(self.c.code_paths[0]).parents[3] / "data" / "MANIFEST.sha256" if self.c.code_paths else None,):
            if cand and cand.exists():
                return hashlib.sha256(cand.read_bytes()).hexdigest()[:16]
        return ""

    @staticmethod
    def definition_of(arm: str, proposer, c: Contract, batch: int = DEFAULT_BATCH) -> dict:
        """what makes an arm this arm, apart from the measuring machinery (that is the epoch): its operators, their guaranteed shares,
        the stage budgets, its promotion rule. A pure function of the arm's proposer and contract, so a check (`ants experiments`) computes it
        without opening a ledger and cannot drift from what a running campaign records. If it changes inside one ledger, results before and
        after are not one experiment. The operators are the REQUESTED ones: an optional operator whose package is missing on this host
        (`unavailable`) stays in the definition, so a restart on a host without it is a degraded run (recorded in daemon_started), not a new
        definition that the guard would answer with exit 4 and a downed service."""
        ops = sorted(set(getattr(proposer, "ops", {}) or [getattr(proposer, "name", "?")]) | set(getattr(proposer, "unavailable", []) or []))
        d = {"arm": arm, "operators": ops, "floors": dict(sorted((getattr(proposer, "floors", {}) or {}).items())),
             "promote_quantile": c.promote_quantile, "promote_by_cap": bool(c.raw.get("cascade", {}).get("promote_by_cap")),
             "min_history": c.min_history, "batch": batch}
        if getattr(proposer, "reserve", None):                    # a key appears only when the arm uses the feature, so older hashes stand
            d["reserve"] = sorted([sorted(k), v] for k, v in proposer.reserve.items())
        if getattr(proposer, "use_discount", False):
            d["use_discount"] = True
        promoter = getattr(proposer, "promoter", None)
        if promoter is not None:                                  # only when present: the definition (and its hash) of every older arm is unchanged
            d["promotion"] = promoter.definition()
        return d

    def arm_definition(self) -> dict:
        return Campaign.definition_of(self.arm, self.p, self.c, self.batch)

    def start_record(self, definition_changed: bool = False, prev_hash: str | None = None) -> dict:
        from .update import current_version
        d = self.arm_definition()
        rec = {"version": current_version(), "epoch": self.epoch, "arm": self.arm, "python": platform.python_version(), "arm_def": d,
               "arm_def_hash": hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]}
        if getattr(self.p, "unavailable", None):                   # outside the hash on purpose: see definition_of
            rec["unavailable"] = sorted(self.p.unavailable)
        if definition_changed:
            rec["definition_changed"] = True
            if prev_hash:
                rec["previous_hash"] = prev_hash
        return rec

    def _epoch(self) -> str:
        i = self._epoch_info()
        ident = {k: i[k] for k in ("code", "contract", "data_manifest")}
        return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12]

    # ---- lifecycle ------------------------------------------------------
    def recover(self) -> list[int]:
        """On start-up (single worker) every 'running' trial belongs to a dead process."""
        ids = self.l.reclaim_stale(self.c.max_attempts, all_running=True)
        if ids:
            self.l.event("recovered", {"arm": self.arm, "trials": ids})
        return ids

    def evals_done(self) -> int:
        return self.l.count_final(self.c.gym, self.arm)

    def budget_left(self) -> bool:
        if self.budget_evals is not None and self.evals_done() >= self.budget_evals:
            return False
        if self.budget_cpu_s is not None and self.l.cpu_total(self.c.gym, self.arm) >= self.budget_cpu_s:
            return False
        return True

    def breaker_open(self, now=None) -> bool:
        """Open after `breaker_n` consecutive bad finishes, for `breaker_cooldown_s`. The cooldown is measured on the monotonic
        clock from the moment the latest failure was seen: an NTP step (backwards: the breaker would stay open for the size of the
        step; forwards: it would close early and hammer a failing gym) must not matter. Wall-clock timestamps are used once, to
        resume a cooldown that was already running when this process started. Passing `now` selects the plain wall-clock rule."""
        last = self.l.recent_final(self.c.gym, self.arm, self.breaker_n)
        if not (len(last) == self.breaker_n and all(st in BAD for st, _ in last)):
            self._brk = None
            return False
        newest = max(ts for _, ts in last)
        if now is not None:
            return now - newest < self.breaker_cooldown_s
        n = self.l.count_final(self.c.gym, self.arm)
        if self._brk is None or self._brk[0] != n:           # a new failure: the cooldown restarts
            age = time.time() - newest
            age = 0.0 if age < 0 else min(age, self.breaker_cooldown_s)          # future stamp: assume it just happened
            self._brk = (n, time.monotonic() - age)
        return time.monotonic() - self._brk[1] < self.breaker_cooldown_s

    # ---- pieces ---------------------------------------------------------
    def _refill(self):
        """Fill the queue. A free analytic cost (e.g. parameter count) screens out proposals that are certain to violate the hard
        cap BEFORE any CPU is spent, except a small, deliberate 'stepping-stone' quota (over-cap specs can seed feasible lineages)."""
        seen = self.l.seen_keys(self.c.gym, self.arm, self.epoch)
        self.hist.update()
        cap = max(self.c.caps) if self.c.caps else None
        need, rounds = self.batch, 0
        while need > 0 and rounds < 6:
            rounds += 1
            props = self.p.propose(self.l, self.arm, need, seen, hist=self.hist)
            if not props:
                break
            for pr in props:
                spec = self.c.space.validate(pr.spec)
                if self.static_mem is not None and self.static_mem(spec) > self.c.mem_cap_mb:   # resource cap: never a stepping stone
                    self.stats["mem_rejected"] = self.stats.get("mem_rejected", 0) + 1
                    continue
                if self.static is not None and cap is not None and self.static(spec) > cap:
                    if self.rng.random() > self.c.stone_quota:
                        self.stats["static_rejected"] += 1
                        continue
                    self.stats["stones_kept"] += 1
                self.n_proposed += 1
                self.l.enqueue(self.c.gym, self.arm, self.seed * 1_000_003 + self.n_proposed, spec, pr.source, priority=0,
                               stage=self.c.stages[0], spec_key=self.c.space.key(spec), origin_key=pr.origin_key,
                               edit=pr.edit, epoch=self.epoch)
                need -= 1

    def _feasible(self, spec: dict) -> bool:
        """The free filters of _refill as a predicate (no stepping stones: a baseline draws only what may be evaluated)."""
        if self.static_mem is not None and self.static_mem(spec) > self.c.mem_cap_mb:
            return False
        cap = max(self.c.caps) if self.c.caps else None
        return not (self.static is not None and cap is not None and self.static(spec) > cap)

    def _timeout(self, stage: str) -> int:
        return self.c.stage_timeouts.get(stage, self.c.trial_timeout_s)

    def _cmd(self, t: dict, out: Path) -> list[str]:
        mp = {"python": self.python, "stage": t["stage"], "spec": str(out / "spec.json"), "out": str(out),
              "seed": str(t["seed"]), "attempt": str(t["attempt"])}
        return [a.format(**mp) for a in self.c.commands["eval"]]

    def _should_promote(self, t: dict, metrics: dict) -> bool:
        i = self.c.stages.index(t["stage"])
        if i + 1 >= len(self.c.stages):
            return False
        if self.c.cap_metric and self.c.caps and metrics.get(self.c.cap_metric, 0) > max(self.c.caps):
            return False                                           # infeasible under the hard cap
        self.hist.update()
        if self.promoter is not None and i == 0:                   # the arm's own rule for the first cut (screen.py); the quantile rule is its fallback
            return self.promoter.decide(self, t, metrics, lambda: self._quantile_rule(t, metrics))
        return self._quantile_rule(t, metrics)

    def _quantile_rule(self, t: dict, metrics: dict) -> bool:
        si = self.c.stages.index(t["stage"])
        # contract [cascade] promote_by_cap = true: a spec competes with the specs of its own size cap, so the small caps are searched too
        # (a global cut sends almost only the largest models deeper: in the first eight hours of the real contract no ≤100 model reached s2)
        bin_ = self.hist.cap_bin(metrics) if (self.c.raw.get("cascade", {}).get("promote_by_cap") and self.c.caps) else None
        n = len(self.hist.stage_sorted.get(si, []) if bin_ is None else self.hist.stage_sorted_cap.get((si, bin_), []))
        if n < max(3, self.c.min_history // 3):
            return True                                            # tiny warm-up: just enough results to have a median
        if n < self.c.min_history:                                 # still building the distribution: promote the better half only
            return self.hist.sign * metrics[self.c.primary] <= self.hist.stage_cut(si, 0.5, bin_)
        cut = self.hist.stage_cut(si, self.c.promote_quantile, bin_)
        return self.hist.sign * metrics[self.c.primary] <= cut

    # ---- riding out transient errors ----------------------------------------
    def _db(self, fn, *a, **kw):
        """Call a ledger write whose payload exists only in memory (a finished evaluation) and ride out transient SQLite / OS
        errors with exponential backoff (~75 s in all). Gives up (raises) only if the error persists or we are being stopped; the
        row then stays 'running' and start-up recovery requeues it: re-evaluated once, never lost, never counted twice."""
        delay = 0.25
        for attempt in range(1, 13):
            try:
                return fn(*a, **kw)
            except Exception as e:
                if not is_transient(e) or attempt == 12 or (attempt >= 3 and self._stop()):
                    raise
                self._count(e)
                self._sleep(delay)
                delay = min(delay * 2, 10)

    def _count(self, e: BaseException):
        self.stats["db_errors" if isinstance(e, sqlite3.Error) else "os_errors"] += 1

    def _finish(self, t: dict, state: str, metrics, error, cpu_s, out_dir) -> bool:
        """finish() with retries. If an earlier try raised AFTER it committed (we cannot know), the retry reports False
        ('not running any more'); the row then tells us whether that was our own write."""
        erred = []

        def go():
            try:
                return self.l.finish(t["id"], t["attempt"], state, metrics, error, cpu_s, out_dir)
            except Exception:
                erred.append(1)
                raise
        ok = self._db(go)
        if not ok and erred:
            row = self.l.get(t["id"])
            return row["state"] == state and row["attempt"] == t["attempt"]
        return ok

    def _execute(self, t: dict) -> str:
        out = self.workdir / "runs" / self.arm / f"{t['id']:07d}_a{t['attempt']}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "spec.json").write_text(json.dumps(t["params"], sort_keys=True))
        env = {k: os.environ[k] for k in self.c.env_passthrough if k in os.environ}
        env["ANTS_ALLOWED_ROLES"] = "dev"          # the search may only read development data (data.Catalog.load enforces it)
        res = run_command(self._cmd(t, out), out, self._timeout(t["stage"]), self.required, env_extra=env,
                          should_stop=self._stop)
        if res.interrupted:        # asked to stop mid-trial: the gym's group is dead, its half-result is not evidence
            self._db(self.l.requeue, t["id"], t["attempt"], "shutdown")
            shutil.rmtree(out, ignore_errors=True)
            return "interrupted"
        if res.metrics is not None:
            full = t["stage"] in self.c.full_record_stages
            if not self._finish(t, "done", res.metrics, None, res.cpu_s, str(out) if full else None):
                return "stale"
            if self._should_promote(t, res.metrics):      # right after the commit: the window in which a kill loses it is tiny
                nxt = self.c.stages[self.c.stages.index(t["stage"]) + 1]
                self._db(self.l.enqueue, self.c.gym, self.arm, t["seed"], t["params"], "promote",
                         priority=100 * (self.c.stages.index(nxt)), stage=nxt, parent_id=t["id"],
                         spec_key=t["spec_key"], epoch=self.epoch)
            if full:       # expensive, decision-grade stages keep files; cheap deterministic ones keep the ledger row only
                (out / "metrics.json").write_text(json.dumps(res.metrics, sort_keys=True))
                for name in ("spec.json", "metrics.json"):
                    self._db(self.l.add_artifact, t["id"], out / name, "metrics", protected=True)
                for gz in compress_logs(out):
                    self._db(self.l.add_artifact, t["id"], gz, "log")
            else:
                shutil.rmtree(out, ignore_errors=True)
            state = "done"
        elif res.timed_out:
            self._finish(t, "timeout", None, res.error, res.cpu_s, str(out)); state = "timeout"
        elif res.signalled and t["attempt"] < self.c.max_attempts:
            self._db(self.l.requeue, t["id"], t["attempt"], f"signal {-res.returncode}"); state = "requeued"
        else:
            st = "crashed" if res.signalled else "failed"
            self._finish(t, st, None, res.error, res.cpu_s, str(out)); state = st
        if state != "done":        # failures always keep their logs
            for gz in compress_logs(out):
                self._db(self.l.add_artifact, t["id"], gz, "log")
        return state

    def _abandon(self, t: dict, err: BaseException):
        """A step blew up after claiming `t`: do not leave it 'running'. Transient trouble and shutdowns requeue it; a trial that
        keeps crashing the supervisor itself is given up on after max_attempts (or it would starve the whole queue)."""
        try:
            row = self.l.get(t["id"])
            if row["state"] != "running" or row["attempt"] != t["attempt"]:
                return
            if isinstance(err, Exception) and not is_transient(err) and t["attempt"] >= self.c.max_attempts:
                self.l.finish(t["id"], t["attempt"], "crashed", None,
                              f"supervisor error: {type(err).__name__}: {err}"[:300], None, None)
            else:
                self.l.requeue(t["id"], t["attempt"], "shutdown" if not isinstance(err, Exception) else f"step error: {type(err).__name__}")
        except Exception:
            self._needs_reclaim = True        # the DB is the problem; the next healthy step reclaims it

    # ---- one step -------------------------------------------------------
    def step(self) -> str:
        if (self.workdir / "PAUSE").exists():                  # `ants pause`: finish nothing new, idle
            return "paused:user"
        if not self.budget_left():
            return "budget"
        lvl = disk_level(self.free_fn(), self.c.thresholds)
        if lvl in ("pause", "hardstop"):
            return f"paused:disk:{lvl}"
        if self.breaker_open():
            return "breaker"
        if self._needs_reclaim:        # a previous step died half-way. One instance per workdir (InstanceLock) and nothing is in
            self.l.reclaim_stale(self.c.max_attempts, all_running=True)       # flight here: every 'running' row is a corpse
            self._needs_reclaim = False
        if self.dream_every and self.dream_fn and self.evals_done() and self.evals_done() % self.dream_every == 0 \
                and getattr(self, "_last_dream", -1) != self.evals_done():
            self._last_dream = self.evals_done()
            self.interruptible = True
            try:
                with keepalive():                  # up to three blocking model calls in turn; systemd's watchdog must not read that as a hang
                    self.dream_fn()
            finally:
                self.interruptible = False
        lease = max(list(self.c.stage_timeouts.values()) + [self.c.trial_timeout_s]) + 60
        t = self.l.claim(lease, self.c.gym, self.arm, epoch=self.epoch)
        if t is None:
            self.interruptible = True              # LLM calls can block for minutes; nothing is in flight, so a stop may interrupt
            try:
                with keepalive():
                    self._refill()
            finally:
                self.interruptible = False
            t = self.l.claim(lease, self.c.gym, self.arm, epoch=self.epoch)
            if t is None:
                return "idle"
        try:
            return f"ran:{t['id']}:{self._execute(t)}"
        except BaseException as e:
            self._abandon(t, e)
            raise

    def _survive(self, e: Exception, streak: int, unexpected: int):
        """Book-keeping for an exception that escaped step(): count it, say so (stderr, and one ledger event a minute), flag the
        reclaim. The caller backs off."""
        self._needs_reclaim = True
        if is_transient(e):
            self._count(e)
        else:
            self.stats["step_errors"] += 1
        msg = f"{type(e).__name__}: {e}"[:300]
        print(f"ants: step failed ({'transient' if is_transient(e) else 'unexpected'}, streak {streak}): {msg}", file=sys.stderr, flush=True)
        if time.monotonic() - self._last_err_event > 60:
            self._last_err_event = time.monotonic()
            try:
                self.l.event("step_error", {"arm": self.arm, "error": msg, "transient": is_transient(e), "streak": streak})
            except Exception:
                pass

    def run(self, max_steps: int | None = None, stop=lambda: False, sleep=None,
            definition_changed: bool = False, prev_hash: str | None = None) -> str:
        if sleep is None:
            def sleep(seconds):                    # wakes every 0.5 s: a 60 s breaker nap must not outlive SIGTERM
                end = time.monotonic() + seconds
                while not stop() and (left := end - time.monotonic()) > 0:
                    time.sleep(min(0.5, left))
        user_stop = stop

        def stop():                                # polled every quarter second while a trial runs and every half second while napping:
            heartbeat()                            # that is the loop's own pulse for systemd's watchdog (no-op outside systemd)
            return user_stop()
        self._stop, self._sleep = stop, sleep
        n, last, streak, unexpected = 0, "idle", 0, 0
        try:
            self.l.event("daemon_started", self.start_record(definition_changed=definition_changed, prev_hash=prev_hash))
        except Exception:
            pass
        sd_notify("READY=1")
        try:
            with keepalive():
                self._db(self.recover)
            while not stop():
                try:
                    last = self.step()
                    streak = unexpected = 0
                except Exception as e:
                    streak += 1
                    unexpected = 0 if is_transient(e) else unexpected + 1
                    self._survive(e, streak, unexpected)
                    if unexpected >= MAX_UNEXPECTED:      # a real bug: die loudly (systemd restarts us) instead of spinning
                        raise
                    sleep(min(60, 0.5 * 2 ** min(streak, 7)))
                    last = "error"
                    continue
                n += 1
                if last in ("budget",) or (max_steps and n >= max_steps):
                    break
                if last.startswith("paused") or last in ("breaker", "idle"):
                    sleep(5 if last in ("idle", "paused:user") else 60)
        except Shutdown:
            pass
        sd_notify("STOPPING=1")
        return last

    # ---- results --------------------------------------------------------
    def frontier(self) -> dict:
        """MAP-Elites-style archive: best spec per cap bin (deepest stage first)."""
        bins: dict = {}
        caps = sorted(self.c.caps) or [float("inf")]
        for r in rank_specs(self.l, self.c, self.arm, epoch=self.epoch):
            cap_val = r["metrics"].get(self.c.cap_metric, 0) if self.c.cap_metric else 0
            for cb in caps:
                if cap_val <= cb and cb not in bins:
                    bins[cb] = r
        return {str(k): v for k, v in bins.items()}

    def freeze_best(self, cap) -> str:
        fr = self.frontier().get(str(cap))
        if not fr:
            raise GateError(f"no candidate under cap {cap}")
        fp = code_fingerprint(self.c.code_paths)
        return self.l.freeze(self.c.gym, fr["spec"], fp, {"trial_id": fr["trial_id"], "value": fr["value"],
                                                         "stage": self.c.stages[fr["stage_idx"]]}, epoch=self.epoch)
