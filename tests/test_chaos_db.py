"""Transient SQLite / OS errors inside Campaign.step must not kill the daemon, lose a trial or count one twice."""
import errno
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from antslab import campaign as campaign_mod
from antslab.campaign import Campaign
from antslab.ledger import Ledger
from antslab.proposers import make_proposer
from tests.test_chaos_common import CLI, procs, query, run_cmd, write_contract  # noqa: F401

MSGS = ["database is locked", "database or disk is full", "disk I/O error"]


def make(ledger, c, tmp_path, **kw):
    return Campaign(ledger, c, tmp_path / "w", "evo", make_proposer("evo", c, 4), 4, **kw)


def flaky(monkeypatch, name, msg, times, after_commit=False):
    """Make Ledger.<name> raise OperationalError(msg) the first `times` calls. after_commit: do the real work first, then raise
    (the nastiest case: the caller cannot tell whether the write happened)."""
    real = getattr(Ledger, name)
    box = {"left": times, "raised": 0}

    def wrapper(self, *a, **kw):
        if box["left"] > 0:
            box["left"] -= 1
            box["raised"] += 1
            if after_commit:
                real(self, *a, **kw)
            raise sqlite3.OperationalError(msg)
        return real(self, *a, **kw)
    monkeypatch.setattr(Ledger, name, wrapper)
    return box


def check_ledger_is_exact(ledger, expect_final):
    rows = ledger.trials()
    assert not [t for t in rows if t["state"] == "running"]
    final = [t for t in rows if t["state"] in ("done", "failed", "timeout", "crashed")]
    assert len(final) >= expect_final
    fin_events = ledger.events("trial_finished")
    ids = [e["payload"]["id"] for e in fin_events]
    assert len(ids) == len(set(ids)), "a trial was finished twice"
    assert sorted(ids) == sorted(t["id"] for t in final), "a final row has no (or an extra) trial_finished event"
    keys = [(t["epoch"], t["spec_key"], t["stage"]) for t in rows if t["state"] == "done"]
    assert len(keys) == len(set(keys)), "a spec was evaluated twice at the same stage"
    assert ledger.verify_chain()[0]


@pytest.mark.parametrize("name", ["claim", "enqueue", "finish", "add_artifact", "count_final", "recent_final", "requeue"])
@pytest.mark.parametrize("msg", MSGS)
def test_transient_errors_are_ridden_out(ledger, make_contract, tmp_path, monkeypatch, name, msg):
    if name == "requeue":
        monkeypatch.setenv("TOY_FAULT", "kill"); monkeypatch.setenv("TOY_FAULT_MOD", "3")
    box = flaky(monkeypatch, name, msg, 3)
    cp = make(ledger, make_contract(), tmp_path, budget_evals=14)
    assert cp.run(sleep=lambda s: None) == "budget"
    assert box["raised"] == 3
    assert cp.stats["db_errors"] >= 3                                           # counted, not silent
    check_ledger_is_exact(ledger, 14)


def test_finish_that_committed_before_raising_is_not_lost_or_doubled(ledger, make_contract, tmp_path, monkeypatch):
    from antslab.ledger import Ledger as L
    control = L(tmp_path / "control" / "ledger.sqlite")
    cc = Campaign(control, make_contract(), tmp_path / "wc", "evo", make_proposer("evo", make_contract(), 4), 4, budget_evals=12)
    cc.run()
    box = flaky(monkeypatch, "finish", "disk I/O error", 2, after_commit=True)
    cp = make(ledger, make_contract(), tmp_path, budget_evals=12)
    assert cp.run(sleep=lambda s: None) == "budget"
    assert box["raised"] == 2
    check_ledger_is_exact(ledger, 12)
    # same trajectory as a fault-free run: the trials whose finish() "failed" were neither lost nor re-run,
    # and their promotions / artifacts were not skipped
    shape = lambda l: sorted((t["spec_key"], t["stage"], t["state"]) for t in l.trials())
    assert shape(ledger) == shape(control)
    assert len(ledger.artifacts("kind='metrics'")) == len(control.artifacts("kind='metrics'"))


def test_persistent_lock_does_not_crash_and_resumes(ledger, make_contract, tmp_path, monkeypatch):
    """The DB is 'locked' for 40 consecutive claim() calls (a long export, a stuck CLI): back off, keep going."""
    box = flaky(monkeypatch, "claim", "database is locked", 40)
    naps = []
    cp = make(ledger, make_contract(), tmp_path, budget_evals=6)
    assert cp.run(sleep=naps.append) == "budget"
    assert box["raised"] == 40 and cp.stats["db_errors"] >= 40
    assert naps and max(naps) <= 60 and max(naps) > min(naps)                   # exponential, capped
    check_ledger_is_exact(ledger, 6)


def test_failed_commit_does_not_wedge_the_connection(tmp_path):
    """COMMIT can fail ('database is locked', I/O error) and leave the transaction open: the next tx() used to die
    forever with 'cannot start a transaction within a transaction'."""
    class Proxy:
        def __init__(self, conn): self._c, self.fail = conn, 1
        def execute(self, sql, *a):
            if sql == "COMMIT" and self.fail:
                self.fail -= 1
                raise sqlite3.OperationalError("database is locked")
            return self._c.execute(sql, *a)
        def __getattr__(self, k): return getattr(self._c, k)
    l = Ledger(tmp_path / "l.sqlite")
    l.event("before", {})
    l.conn = Proxy(l.conn)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        l.event("lost", {})
    l.event("after", {})                                                         # must work
    assert [e["kind"] for e in l.events()] == ["before", "after"]               # the failed write left no trace
    assert l.verify_chain()[0]


def test_rollback_after_autorollback_does_not_mask_the_error(tmp_path):
    """SQLite sometimes rolls back by itself (disk full): our cleanup ROLLBACK must not replace the real error."""
    class Proxy:
        def __init__(self, conn): self._c = conn
        def execute(self, sql, *a):
            if sql == "COMMIT":
                self._c.execute("ROLLBACK")
                raise sqlite3.OperationalError("database or disk is full")
            return self._c.execute(sql, *a)
        def __getattr__(self, k): return getattr(self._c, k)
    l = Ledger(tmp_path / "l.sqlite")
    l.conn = Proxy(l.conn)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        l.event("x", {})


@pytest.mark.parametrize("err", [OSError(errno.ENOSPC, "No space left on device"), OSError(errno.EAGAIN, "fork: Resource temporarily unavailable"),
                                 OSError(errno.ENOMEM, "Cannot allocate memory")])
def test_transient_os_errors_while_spawning_a_gym(ledger, make_contract, tmp_path, monkeypatch, err):
    real, box = campaign_mod.run_command, {"n": 0}

    def flaky_spawn(*a, **kw):
        box["n"] += 1
        if box["n"] <= 3:
            raise err
        return real(*a, **kw)
    monkeypatch.setattr(campaign_mod, "run_command", flaky_spawn)
    cp = make(ledger, make_contract(), tmp_path, budget_evals=6)
    assert cp.run(sleep=lambda s: None) == "budget"
    check_ledger_is_exact(ledger, 6)


def test_unexpected_error_mid_trial_leaves_no_running_row(ledger, make_contract, tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(campaign_mod, "run_command", boom)
    cp = make(ledger, make_contract(), tmp_path, batch=1)
    with pytest.raises(RuntimeError):
        cp.step()
    assert not [t for t in ledger.trials() if t["state"] == "running"]


def test_poison_trial_is_given_up_on_and_the_loop_moves_on(ledger, make_contract, tmp_path, monkeypatch):
    real = campaign_mod.run_command

    def poison_first(cmd, out, *a, **kw):
        if out.name.startswith("0000001_"):
            raise RuntimeError("deterministic bug in trial 1")
        return real(cmd, out, *a, **kw)
    monkeypatch.setattr(campaign_mod, "run_command", poison_first)
    cp = make(ledger, make_contract(), tmp_path, budget_evals=8)
    assert cp.run(sleep=lambda s: None) == "budget"
    t1 = ledger.get(1)
    assert t1["state"] == "crashed" and "deterministic bug" in t1["error"]
    assert ledger.verify_chain()[0]


@pytest.mark.slow
def test_two_processes_hammer_one_ledger_for_20s(tmp_path, procs):
    """The daemon plus CLI processes writing hints / exporting the same ledger concurrently: no corruption, no crash."""
    contract, wd = write_contract(tmp_path), tmp_path / "wd"
    d = procs(run_cmd(contract, wd, max_evals=100000), env={**os.environ, "TOY_SLEEP": "0.02"})
    end = time.time() + 20
    codes, n_hint = [], 0
    helper = tmp_path / "hammer.py"
    helper.write_text(
        "import sys, time\nfrom antslab.ledger import Ledger\nl = Ledger(sys.argv[1]); end = time.time() + float(sys.argv[2]); n = 0\n"
        "while time.time() < end:\n    l.event('user_hint', {'text': f'hammer {n}'}); n += 1\nprint(n)\n")
    h = procs([sys.executable, str(helper), str(wd / "ledger.sqlite"), "18"])
    time.sleep(2)
    base = ["--contract", str(contract), "--workdir", str(wd), "--arm", "evo"]
    while time.time() < end and d.poll() is None:
        for argv in (["hint", *base, f"cli hint {n_hint}"], ["export", *base, "--out", str(tmp_path / "exp")], ["status", *base]):
            r = subprocess.run(CLI + argv, capture_output=True, text=True, timeout=120)
            codes.append((argv[0], r.returncode, r.stderr[-300:]))
        n_hint += 1
    assert d.poll() is None, "the daemon died while the ledger was being used by other processes"
    bad = [c for c in codes if c[1] != 0]
    assert not bad, bad[:3]
    h.wait(timeout=60)
    assert h.returncode == 0, h.stdout.read()
    d.terminate()
    d.wait(timeout=30)
    l = Ledger(wd / "ledger.sqlite")
    assert l.verify_chain() == (True, None)
    assert not [t for t in l.trials() if t["state"] == "running"]
    assert len(l.events("trial_finished")) > 20
    assert sum(1 for e in l.events("user_hint")) >= n_hint + 50


@pytest.mark.parametrize("e,expect", [
    (sqlite3.OperationalError("database is locked"), True), (sqlite3.OperationalError("database table is locked"), True),
    (sqlite3.OperationalError("database or disk is full"), True), (sqlite3.OperationalError("disk I/O error"), True),
    (sqlite3.OperationalError("unable to open database file"), True),
    (sqlite3.DatabaseError("database disk image is malformed"), False),          # corruption is not "try again later"
    (sqlite3.OperationalError("cannot start a transaction within a transaction"), False),
    (sqlite3.OperationalError("no such table: trials"), False),
    (OSError(errno.ENOSPC, "x"), True), (OSError(errno.EAGAIN, "x"), True), (OSError(errno.ENOENT, "x"), False),
    (RuntimeError("database is locked"), False), (KeyError("x"), False),
])
def test_transient_classification(e, expect):
    from antslab.campaign import is_transient
    assert is_transient(e) is expect


def test_repeated_unexpected_errors_still_exit_nonzero_instead_of_spinning(ledger, make_contract, tmp_path, monkeypatch):
    """Survival is for transient trouble. A real bug (here: every claim raises KeyError) must end the process so systemd
    restarts it and `systemctl status` shows the failure; it must not loop silently for weeks."""
    monkeypatch.setattr(Ledger, "claim", lambda self, *a, **k: (_ for _ in ()).throw(KeyError("bug")))
    cp = make(ledger, make_contract(), tmp_path)
    with pytest.raises(KeyError):
        cp.run(sleep=lambda s: None)
    assert cp.stats["step_errors"] == 5
