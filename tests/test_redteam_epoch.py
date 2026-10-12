"""Red team: evaluator or data changes mid-run - can old-epoch results influence ranking, promotion or the sealed evaluation?"""
import shutil

import pytest
from antslab.contract import code_fingerprint

from antslab import gate
from antslab.campaign import Campaign
from antslab.ledger import GateError
from antslab.proposers import make_proposer, rank_specs


def _two_epochs(ledger, make_contract, tmp_path, penalty=5.0):
    """Epoch 1: the real toy gym. Epoch 2: the SAME ledger and arm after an edit that makes every value `penalty` worse."""
    toy = tmp_path / "toy_copy.py"
    shutil.copy(make_contract().code_paths[0], toy)
    c1 = make_contract(toy=toy)
    camp1 = Campaign(ledger, c1, tmp_path / "w", "random", make_proposer("random", c1, 0), 0, budget_evals=30)
    camp1.run()
    toy.write_text(toy.read_text().replace("val += rng.gauss(0, noise)", f"val += rng.gauss(0, noise) + {penalty}"))
    c2 = make_contract(toy=toy)
    camp2 = Campaign(ledger, c2, tmp_path / "w", "random", make_proposer("random", c2, 1), 1, budget_evals=60)
    camp2.run()
    assert camp1.epoch != camp2.epoch
    return camp1, camp2, c2


def test_ranking_and_frontier_only_use_the_current_epoch(ledger, make_contract, tmp_path):
    camp1, camp2, c2 = _two_epochs(ledger, make_contract, tmp_path)
    mixed = rank_specs(ledger, c2, "random")                          # unfiltered ranking still mixes epochs (tools may want that)
    assert {ledger.get(r["trial_id"])["epoch"] for r in mixed} == {camp1.epoch, camp2.epoch}
    own = rank_specs(ledger, c2, "random", epoch=camp2.epoch)
    assert {ledger.get(r["trial_id"])["epoch"] for r in own} == {camp2.epoch}
    assert all(ledger.get(r["trial_id"])["epoch"] == camp2.epoch for r in camp2.frontier().values())
    assert camp2.hist.epoch == camp2.epoch
    assert all(ledger.get(i)["epoch"] == camp2.epoch for i in [r["id"] for r in camp2.hist.best.values()])


def test_a_new_epoch_remeasures_specs_the_old_epoch_had_seen(ledger, make_contract, tmp_path):
    camp1, camp2, c2 = _two_epochs(ledger, make_contract, tmp_path)
    assert ledger.seen_keys(c2.gym, "random", camp2.epoch) != ledger.seen_keys(c2.gym, "random")
    assert ledger.seen_keys(c2.gym, "random", camp1.epoch)            # the old epoch's keys are still there, just not 'seen' now


def test_freezing_the_best_of_a_stale_epoch_under_todays_code_is_refused(ledger, make_contract, tmp_path):
    camp1, camp2, c2 = _two_epochs(ledger, make_contract, tmp_path)
    stale = rank_specs(ledger, c2, "random", epoch=camp1.epoch)[0]
    fp = code_fingerprint(c2.code_paths)
    with pytest.raises(GateError, match="different evaluator code"):
        ledger.freeze(c2.gym, stale["spec"], fp, {"trial_id": stale["trial_id"], "value": stale["value"], "stage": "s0"})
    assert ledger.events("candidate_frozen") == []                    # nothing was frozen, so nothing can be sealed
    with pytest.raises(GateError):
        gate.seal(ledger, c2, tmp_path / "w", "0" * 64)
