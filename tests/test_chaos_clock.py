"""Clock anomalies (NTP step, VM booted with a wrong time): leases, the circuit breaker and 'hot' cleanup windows."""
import time

import pytest

from antslab.campaign import Campaign
from antslab.history import History
from antslab.janitor import plan_cleanup
from antslab.ledger import Ledger
from antslab.proposers import make_proposer


class Clock:
    """Wall clock with a settable offset (an NTP step) and a separate monotonic offset (real elapsed time)."""
    def __init__(self, monkeypatch):
        self.wall, self.mono = 0.0, 0.0
        rt, rm = time.time, time.monotonic
        monkeypatch.setattr(time, "time", lambda: rt() + self.wall)
        monkeypatch.setattr(time, "monotonic", lambda: rm() + self.mono)


def camp(ledger, c, tmp_path, **kw):
    return Campaign(ledger, c, tmp_path / "w", "evo", make_proposer("evo", c, 1), 1, batch=1, **kw)


@pytest.fixture
def failing(monkeypatch):
    monkeypatch.setenv("TOY_FAULT", "exit"); monkeypatch.setenv("TOY_FAULT_MOD", "1")


def test_breaker_reopens_on_schedule_after_the_clock_steps_back(ledger, make_contract, tmp_path, monkeypatch, failing):
    clk = Clock(monkeypatch)
    cp = camp(ledger, make_contract(), tmp_path, breaker_n=3, breaker_cooldown_s=3600)
    clk.wall = 86400                                         # the VM booted a day fast
    assert [cp.step().split(":")[-1] for _ in range(3)] == ["failed"] * 3
    assert cp.step() == "breaker"
    clk.wall = 0                                             # NTP corrects it: every finished_at is now a day in the future
    clk.mono += 60
    assert cp.step() == "breaker"                            # cooldown still honoured
    clk.mono += 3600
    assert cp.step().startswith("ran:"), "breaker stayed open for a day because of the stepped-back clock"


def test_breaker_does_not_close_early_when_the_clock_jumps_forward(ledger, make_contract, tmp_path, monkeypatch, failing):
    clk = Clock(monkeypatch)
    cp = camp(ledger, make_contract(), tmp_path, breaker_n=3, breaker_cooldown_s=3600)
    for _ in range(3):
        cp.step()
    assert cp.step() == "breaker"
    clk.wall += 86400                                        # NTP steps forward a day; only seconds really passed
    assert cp.step() == "breaker", "a forward clock step bypassed the cooldown and hammered a failing gym"


def test_finished_at_and_event_time_never_go_backwards(ledger, monkeypatch):
    clk = Clock(monkeypatch)
    stamps = []
    for i, step in enumerate((0, 5000, -86400, 10)):         # forward, then a day backwards, then normal progress
        clk.wall += step
        ledger.enqueue("g", "a", i, {"x": i}, "t", spec_key=f"k{i}")
        t = ledger.claim(60, arm="a")
        assert ledger.finish(t["id"], t["attempt"], "done", {"v": 1.0})
        stamps.append(ledger.get(t["id"])["finished_at"])
    assert stamps == sorted(stamps), stamps
    ts = [e["ts"] for e in ledger.events()]
    assert ts == sorted(ts)
    assert [s for s, _ in ledger.recent_final("g", "a", 2)] == ["done", "done"]
    # the order recent_final / History rely on is the true completion order
    last2 = [r[1] for r in ledger.recent_final("g", "a", 2)]
    assert last2[0] >= last2[1] >= stamps[0]
    assert ledger.verify_chain()[0]


def test_history_keeps_learning_after_the_clock_steps_back(ledger, make_contract, monkeypatch):
    """History tails the ledger by finished_at: results stamped before its cursor used to be ignored until the clock caught up."""
    clk = Clock(monkeypatch)
    c = make_contract()
    h = History(ledger, c, "a")
    for i in range(6):
        if i == 3:
            clk.wall -= 7200
        ledger.enqueue("toy", "a", i, {"a": 0.1 * i, "b": 3, "c": "u"}, "x", spec_key=f"k{i}")
        t = ledger.claim(60, arm="a")
        ledger.finish(t["id"], t["attempt"], "done", {"val_mse": 1.0 + i, "n_params": 30})
        h.update()
    assert h.n == 6


def test_future_verified_at_is_never_deleted(ledger, tmp_path):
    """verified_at in the future (clock stepped back after verification) must read as 'hot', not as 'old'."""
    tid = ledger.enqueue("g", "a", 1, {}, "t")
    ledger.conn.execute("UPDATE trials SET state='done' WHERE id=?", (tid,))
    f = tmp_path / "f.log.gz"
    f.write_text("x" * 100)
    ledger.add_artifact(tid, f, "log")
    ledger.mark_archived(str(f), "file://x", ledger.artifact(str(f))["sha256"])
    now = time.time()
    for verified in (now + 60, now + 86400, now + 1e9, now - 3600, now - 7 * 3600):
        ledger.conn.execute("UPDATE artifacts SET verified_at=?", (verified,))
        planned = plan_cleanup(ledger, 10, hot_s=6 * 3600, now=now)
        assert bool(planned) == (verified <= now - 6 * 3600), verified


def test_time_jumping_during_a_run_never_reclaims_or_repeats_a_live_trial(ledger, make_contract, tmp_path, monkeypatch):
    clk = Clock(monkeypatch)
    cp = camp(ledger, make_contract(), tmp_path, budget_evals=12)
    calls = {"n": 0}
    real_step = cp.step

    def jumpy():
        calls["n"] += 1
        clk.wall += 86400 if calls["n"] % 3 == 0 else -50000 if calls["n"] % 3 == 1 else 0
        return real_step()
    cp.step = jumpy
    cp.run(sleep=lambda s: None)
    kinds = [e["kind"] for e in ledger.events()]
    assert "trial_reclaimed" not in kinds and "trial_requeued" not in kinds
    keys = [(t["spec_key"], t["stage"]) for t in ledger.trials(states=("done",))]
    assert len(keys) == len(set(keys)) and ledger.verify_chain()[0]
