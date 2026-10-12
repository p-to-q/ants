import shutil
import pytest
from antslab.ledger import GateError, UnsealBudgetExceeded
from antslab.gate import seal
from antslab.campaign import Campaign
from antslab.proposers import make_proposer


def test_chain_detects_tamper(ledger):
    for i in range(3):
        ledger.event("e", {"x": i})
    assert ledger.verify_chain() == (True, None)
    ledger.conn.execute("UPDATE events SET payload='{\"x\":999}' WHERE seq=2")
    ok, bad = ledger.verify_chain()
    assert not ok and bad == 2


def test_chain_detects_deleted_middle_row(ledger):
    for i in range(4):
        ledger.event("e", {"x": i})
    ledger.conn.execute("DELETE FROM events WHERE seq=2")
    assert ledger.verify_chain()[0] is False


def test_fencing_token_blocks_stale_writer(ledger):
    ledger.enqueue("g", "a", 1, {"x": 1}, "t")
    t1 = ledger.claim(60)
    assert ledger.reclaim_stale(3, all_running=True) == [t1["id"]]
    t2 = ledger.claim(60)
    assert t2["attempt"] == 2
    assert ledger.finish(t1["id"], 1, "done", {"v": 1}) is False      # stale runner rejected
    assert ledger.finish(t2["id"], 2, "done", {"v": 2}) is True
    assert ledger.get(t1["id"])["metrics"] == {"v": 2}


def test_reclaim_gives_up_after_max_attempts(ledger):
    ledger.enqueue("g", "a", 1, {"x": 1}, "t")
    ledger.claim(60); ledger.reclaim_stale(2, all_running=True)
    ledger.claim(60); ledger.reclaim_stale(2, all_running=True)
    assert ledger.trials()[0]["state"] == "crashed"


def test_lease_not_expired_is_left_alone(ledger):
    ledger.enqueue("g", "a", 1, {"x": 1}, "t")
    ledger.claim(1000)
    assert ledger.reclaim_stale(3) == []


def _campaign(ledger, c, tmp_path, arm="random", **kw):
    return Campaign(ledger, c, tmp_path / "w", arm, make_proposer(arm, c, 0), 0, **kw)


def test_seal_requires_freeze(ledger, make_contract, tmp_path):
    c = make_contract()
    with pytest.raises(GateError):
        seal(ledger, c, tmp_path / "w", "deadbeef")


def test_seal_happy_path_then_budget_exhausted(ledger, make_contract, tmp_path):
    c = make_contract()
    camp = _campaign(ledger, c, tmp_path, budget_evals=25)
    camp.run()
    fh = camp.freeze_best(300)
    m = seal(ledger, c, tmp_path / "w", fh)
    assert "val_mse" in m and ledger.unseal_count("toy") == 1
    with pytest.raises(UnsealBudgetExceeded):
        seal(ledger, c, tmp_path / "w", fh)
    assert ledger.verify_chain()[0]


def test_seal_refuses_if_evaluator_code_changed(ledger, make_contract, tmp_path):
    toy_copy = tmp_path / "toy_copy.py"
    shutil.copy(make_contract().code_paths[0], toy_copy)
    c = make_contract(toy=toy_copy)
    camp = _campaign(ledger, c, tmp_path, budget_evals=20)
    camp.run()
    fh = camp.freeze_best(300)
    toy_copy.write_text(toy_copy.read_text() + "\n# tampered\n")
    with pytest.raises(GateError, match="code changed"):
        seal(ledger, c, tmp_path / "w", fh)
    assert ledger.unseal_count("toy") == 0                            # nothing was spent
