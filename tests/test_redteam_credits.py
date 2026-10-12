"""Red team: sealed-credit accounting (who may look at the sealed data, how often, and is every look on the books)."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from antslab import gate
from antslab.campaign import Campaign
from antslab.contract import code_fingerprint
from antslab.ledger import GateError, UnsealBudgetExceeded
from antslab.proposers import make_proposer

SRC = str(Path(__file__).resolve().parents[1] / "src")


def evidence_trial(ledger, gym, spec, epoch, code="code0"):
    """A finished trial of `epoch` (registered with evaluator-code fingerprint `code`), as the campaign would leave it."""
    ledger.register_epoch(epoch, {"code": code[:16], "contract": "c"})
    tid = ledger.enqueue(gym, "arm", 1, spec, "t", epoch=epoch)
    t = ledger.claim(60)
    assert ledger.finish(t["id"], t["attempt"], "done", {"v": 1.0}, cpu_s=0.0)
    return tid


def test_the_same_candidate_gets_one_look_even_when_the_contract_allows_several(ledger):
    fh = ledger.freeze("g", {"x": 1}, "code0", {})
    assert ledger.freeze("g", {"x": 1}, "code0", {}) == fh                      # freezing twice is idempotent...
    assert len(ledger.events("candidate_frozen")) == 1
    assert ledger.record_unseal("g", fh, 3, {}) == 1
    with pytest.raises(UnsealBudgetExceeded, match="already had its sealed look"):
        ledger.record_unseal("g", fh, 3, {})                                     # ...and a second look is only a re-roll
    assert ledger.unseal_count("g") == 1


def test_different_candidates_share_the_budget_of_the_same_sealed_set(ledger):
    a, b = ledger.freeze("g", {"x": 1}, "c", {}), ledger.freeze("g", {"x": 2}, "c", {})
    ledger.record_unseal("g", a, 1, {}, sealed_set="S")
    with pytest.raises(UnsealBudgetExceeded):
        ledger.record_unseal("g", b, 1, {}, sealed_set="S")
    ledger.record_unseal("g", b, 1, {}, sealed_set="T")                           # a different sealed set has its own credit


def test_looks_without_a_freeze_are_refused(ledger):
    with pytest.raises(GateError, match="never frozen"):
        ledger.record_unseal("g", "deadbeef", 1, {})
    assert ledger.unseal_count("g") == 0


def test_credits_are_per_epoch_but_capped_over_the_lifetime_of_the_sealed_set(ledger):
    hashes = []
    for i, ep in enumerate(("e1", "e2", "e3")):
        tid = evidence_trial(ledger, "g", {"x": i}, ep)
        hashes.append(ledger.freeze("g", {"x": i}, "code0", {"trial_id": tid}))
    assert len(set(hashes)) == 3                                                  # the epoch is part of the freeze identity
    assert ledger.record_unseal("g", hashes[0], 1, {}, "S", lifetime=2) == 1
    assert ledger.record_unseal("g", hashes[1], 1, {}, "S", lifetime=2) == 1      # new epoch: a fresh per-epoch credit...
    with pytest.raises(UnsealBudgetExceeded, match="lifetime"):
        ledger.record_unseal("g", hashes[2], 1, {}, "S", lifetime=2)              # ...but peeking, tweaking code, peeking is capped


def test_limits_can_be_lowered_but_never_raised_after_the_first_look(ledger):
    a, b = ledger.freeze("g", {"x": 1}, "c", {}), ledger.freeze("g", {"x": 2}, "c", {})
    ledger.record_unseal("g", a, 1, {}, "S", lifetime=1)
    with pytest.raises(UnsealBudgetExceeded):                                      # editing the contract to unseal_limit=5 buys nothing
        ledger.record_unseal("g", b, 5, {}, "S", lifetime=9)


def test_freeze_refuses_evidence_that_belongs_to_another_spec_gym_or_epoch(ledger):
    tid = evidence_trial(ledger, "g", {"x": 1}, "e1", code="code0")
    with pytest.raises(GateError, match="differs"):
        ledger.freeze("g", {"x": 999}, "code0", {"trial_id": tid})                 # someone else's evidence for my spec
    with pytest.raises(GateError, match="does not exist"):
        ledger.freeze("other", {"x": 1}, "code0", {"trial_id": tid})
    with pytest.raises(GateError, match="different evaluator code"):
        ledger.freeze("g", {"x": 1}, "code1", {"trial_id": tid})                   # measured under code0, frozen under code1
    with pytest.raises(GateError, match="epoch"):
        ledger.freeze("g", {"x": 1}, "code0", {"trial_id": tid}, epoch="e2")


def test_concurrent_processes_cannot_overspend_the_credit(tmp_path):
    from antslab.ledger import Ledger
    path = tmp_path / "ledger.sqlite"
    led = Ledger(path)
    hashes = [led.freeze("g", {"x": i}, "c", {}) for i in range(8)]
    led.close()
    script = textwrap.dedent("""
        import sys
        from antslab.ledger import Ledger, UnsealBudgetExceeded
        led = Ledger(sys.argv[1])
        try:
            led.record_unseal("g", sys.argv[2], 2, {}, "S", lifetime=2); print("granted")
        except UnsealBudgetExceeded:
            print("refused")
    """)
    env = {**os.environ, "PYTHONPATH": SRC}
    procs = [subprocess.Popen([sys.executable, "-c", script, str(path), h], env=env, stdout=subprocess.PIPE, text=True) for h in hashes]
    outs = [p.communicate()[0].strip() for p in procs]
    assert outs.count("granted") == 2 and outs.count("refused") == 6
    led = Ledger(path)
    assert led.unseal_count("g") == 2 and led.verify_chain() == (True, None)


def test_a_sealed_result_needs_a_look_and_a_look_gets_one_result(ledger):
    fh = ledger.freeze("g", {"x": 1}, "c", {})
    with pytest.raises(GateError, match="without a recorded look"):
        ledger.record_sealed_result("g", fh, 1, {"v": 0.1}, None)
    n = ledger.record_unseal("g", fh, 1, {})
    assert ledger.sealed_result("g", fh) is None                                  # spent but not finished: no evidence yet
    ledger.record_sealed_result("g", fh, n, {"v": 0.1}, None)
    with pytest.raises(GateError, match="already has a result"):
        ledger.record_sealed_result("g", fh, n, {"v": 0.0}, None)
    assert ledger.sealed_result("g", fh)["metrics"] == {"v": 0.1}


def test_a_failed_sealed_run_has_no_usable_result(ledger):
    fh = ledger.freeze("g", {"x": 1}, "c", {})
    n = ledger.record_unseal("g", fh, 1, {})
    ledger.record_sealed_result("g", fh, n, None, "exit 1: boom")
    assert ledger.sealed_result("g", fh) is None


def _campaign(ledger, c, tmp_path):
    return Campaign(ledger, c, tmp_path / "w", "random", make_proposer("random", c, 0), 0, budget_evals=20)


def test_a_crash_inside_the_gate_still_closes_the_look_and_burns_the_credit(ledger, make_contract, tmp_path, monkeypatch):
    c = make_contract()
    camp = _campaign(ledger, c, tmp_path); camp.run()
    fh = camp.freeze_best(300)
    monkeypatch.setattr(gate, "run_command", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        gate.seal(ledger, c, tmp_path / "w", fh)
    res = ledger.events("sealed_result")
    assert len(res) == 1 and res[0]["payload"]["metrics"] is None and "disk full" in res[0]["payload"]["error"]
    monkeypatch.undo()
    with pytest.raises(UnsealBudgetExceeded):
        gate.seal(ledger, c, tmp_path / "w", fh)                                  # no second look after the crash
    assert ledger.sealed_result("toy", fh) is None


def test_seal_records_the_sealed_set_epoch_and_result_against_the_freeze_hash(ledger, make_contract, tmp_path):
    c = make_contract()
    c.raw["sealed"] = {"datasets": ["alpha", "beta"]}
    camp = _campaign(ledger, c, tmp_path); camp.run()
    fh = camp.freeze_best(300)
    m = gate.seal(ledger, c, tmp_path / "w", fh)
    look = ledger.events("sealed_evaluation")[0]["payload"]
    assert look["freeze_hash"] == fh and look["epoch"] == camp.epoch and look["sealed_set"] == gate.sealed_set_id(c) != ""
    assert ledger.sealed_result("toy", fh)["metrics"] == m
    c.raw["sealed"] = {"datasets": ["alpha", "gamma"]}
    assert gate.sealed_set_id(c) != look["sealed_set"]


def test_seal_refuses_if_the_contract_changed_after_the_freeze(ledger, make_contract, tmp_path):
    c = make_contract()
    camp = _campaign(ledger, c, tmp_path); camp.run()
    fh = camp.freeze_best(300)
    c.raw["budget"]["unseal_limit"] = 9                                           # "just give me more looks"
    with pytest.raises(GateError, match="contract changed"):
        gate.seal(ledger, c, tmp_path / "w", fh)
    assert ledger.unseal_count("toy") == 0


def test_the_final_run_sees_only_the_roles_it_is_entitled_to(ledger, make_contract, tmp_path, monkeypatch):
    seen = {}
    real = gate.run_command
    def spy(cmd, out, timeout, required, env_extra=None, **kw):
        seen.update(env_extra or {})
        return real(cmd, out, timeout, required, env_extra=env_extra, **kw)
    monkeypatch.setattr(gate, "run_command", spy)
    c = make_contract()
    camp = _campaign(ledger, c, tmp_path); camp.run()
    gate.seal(ledger, c, tmp_path / "w", camp.freeze_best(300))
    assert seen["ANTS_ALLOWED_ROLES"] == "dev,confirm,sealed"


def test_seal_refuses_a_broken_chain_and_a_ledger_that_forgot_its_anchors(ledger, make_contract, tmp_path):
    c = make_contract()
    camp = _campaign(ledger, c, tmp_path); camp.run()
    fh = camp.freeze_best(300)
    anchors = tmp_path / "anchors.jsonl"
    ledger.write_anchor(anchors)                                                    # taken at freeze time, kept off-box
    ledger.event("noise", {"a": 1}); ledger.write_anchor(anchors)
    ledger.conn.execute("DELETE FROM events WHERE seq=(SELECT MAX(seq) FROM events)")      # forget the last event: chain stays valid
    assert ledger.verify_chain()[0]
    with pytest.raises(GateError, match="external anchors"):
        gate.seal(ledger, c, tmp_path / "w", fh, anchor_file=anchors)
    assert ledger.unseal_count("toy") == 0
    ledger.conn.execute("UPDATE events SET payload='{}' WHERE seq=2")
    with pytest.raises(GateError, match="chain broken"):
        gate.seal(ledger, c, tmp_path / "w", fh)


def test_seal_appends_an_anchor_after_the_result(ledger, make_contract, tmp_path):
    c = make_contract()
    camp = _campaign(ledger, c, tmp_path); camp.run()
    fh = camp.freeze_best(300)
    anchors = tmp_path / "anchors.jsonl"
    gate.seal(ledger, c, tmp_path / "w", fh, anchor_file=anchors)
    line = json.loads(anchors.read_text().splitlines()[-1])
    assert line["seq"] == ledger.anchor().seq and line["head"] == ledger.head()
    assert ledger.verify_anchor_file(anchors) == (True, None)
