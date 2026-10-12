"""Regressions for defects found by the independent audit (history truncation, malformed model output)."""
import random
import time

import pytest

from antslab.dream import dream
from antslab.history import History
from antslab.proposers import EvoProposer, LLMProposer


def test_history_update_is_complete_beyond_one_batch(ledger, make_contract):
    c = make_contract()
    n = 450
    for i in range(n):
        ledger.enqueue("toy", "h", i, {"a": (i % 97) / 97, "b": 1 + i % 30, "c": "u"}, "x", spec_key=f"k{i}")
        t = ledger.claim(60, arm="h")
        ledger.finish(t["id"], 1, "done", {"val_mse": 1.0 - i / 1000, "n_params": 30})
    h = History(ledger, c, "h")
    h.BATCH = 100                                    # force several batches
    assert h.update() == n and h.n == n
    assert h.update() == 0
    assert abs(min(r["value"] for r in h.best.values()) - (1.0 - (n - 1) / 1000)) < 1e-9     # the newest (best) rows were seen


class _Client:
    def __init__(self, reply):
        self.reply = reply

    def chat_json(self, messages, temperature=0.9):
        return self.reply, {"ep": "A"}


@pytest.mark.parametrize("reply", [{"lessons": ["x"]}, {"lessons": None}, {"lessons": "x"}, ["x"], {"lessons": [None, 3]}, "text"])
def test_dream_survives_structurally_wrong_model_output(ledger, make_contract, reply):
    c = make_contract()
    for i in range(6):
        ledger.enqueue("toy", "h", i, {"a": i / 10, "b": 3, "c": "u"}, "x", spec_key=f"k{i}")
        t = ledger.claim(60, arm="h")
        ledger.finish(t["id"], 1, "done", {"val_mse": 1.0 + i, "n_params": 30})
    assert isinstance(dream(ledger, c, "h", client=_Client(reply)), list)


@pytest.mark.parametrize("reply", [{"proposals": {"a": 1}}, {"proposals": "x"}, ["x"], {"proposals": [1, None, "z"]}, 5])
def test_malformed_reply_never_leaks_inflight(ledger, make_contract, reply):
    c = make_contract()
    p = LLMProposer(c, _Client(reply), EvoProposer(c, random.Random(0)), random.Random(0), per_call=4)
    for _ in range(8):                               # more than max_inflight: a leak would disable LLM proposals for good
        p.refill(ledger, "arm", set())
        for _ in range(100):
            time.sleep(0.01)
            with p.lock:
                if p.inflight == 0:
                    break
    assert p.inflight == 0 and p.stats["calls"] >= 5


LOOP_MODULES = ["ledger", "contract", "runner", "history", "proposers", "portfolio", "credit", "lineage", "landscape",
                "archive", "janitor", "llm", "campaign", "ui", "board", "cli", "gate", "report", "dream"]


def test_search_loop_imports_without_numpy():
    """The documented promise: the search loop is standard library only (statistics, data and gyms need numpy)."""
    import subprocess
    import sys
    code = ("import sys; sys.modules['numpy']=None; sys.modules['scipy']=None\n"
            + "\n".join(f"import antslab.{m}" for m in LOOP_MODULES))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


def test_random_baseline_samples_the_feasible_region_instead_of_starving(ledger, make_contract):
    """With a feasibility predicate that accepts ~1% of draws, the random arm still delivers k proposals, all feasible."""
    from antslab.proposers import RandomProposer
    c = make_contract()
    p = RandomProposer(c, random.Random(0))
    p.accept = lambda s: s["a"] < 0.01
    out = p.propose(ledger, "arm", 8, set())
    assert len(out) == 8 and all(x.spec["a"] < 0.01 for x in out)
    plain = RandomProposer(c, random.Random(0))
    assert len(plain.propose(ledger, "arm", 8, set())) == 8          # no predicate: unchanged behaviour


def test_campaign_hands_random_and_tpe_the_feasibility_predicate(ledger, make_contract, tmp_path):
    from antslab.campaign import Campaign
    from antslab.proposers import make_proposer
    c = make_contract()
    p = make_proposer("random", c, 1)
    camp = Campaign(ledger, c, tmp_path / "w", "random", p, 1, budget_evals=1)
    assert p.accept == camp._feasible and callable(p.accept)
