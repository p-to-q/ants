import json, random
import numpy as np
import pytest
from antslab import judge as J
from antslab.history import History


def test_calibration_metrics_known_values():
    y = [1, 1, 0, 0]
    assert J.auc([0.9, 0.8, 0.2, 0.1], y) == 1.0 and J.auc([0.5] * 4, y) == 0.5
    assert J.brier([1, 1, 0, 0], y) == 0.0
    assert J.ece([0.9] * 100, [1] * 90 + [0] * 10) < 1e-9          # perfectly calibrated
    assert J.ece([0.9] * 100, [1] * 50 + [0] * 50) > 0.35           # over-confident


def _fill(ledger, judge, p_fn, n, seed=0):
    rnd = random.Random(seed)
    for i in range(n):
        y = rnd.random() < 0.2
        jid = ledger.log_judgement(judge, "k", f"s{i}", p_fn(y, rnd), "yes", "{}", "shadow")
        ledger.resolve_judgement(jid, y)


def test_authority_is_earned_by_calibration_not_granted(ledger):
    assert J.authority(ledger, "oracle", "k") == "shadow"                      # no data
    _fill(ledger, "ranker", lambda y, r: 0.85 if y else 0.1, 300)              # perfect ordering, ECE ~0.11 > 0.10
    assert J.calibration(ledger, "ranker", "k")["auc"] > 0.95
    assert J.authority(ledger, "ranker", "k") == "shadow"                      # informative but miscalibrated: no rights
    _fill(ledger, "coin", lambda y, r: r.random(), 400, seed=1)                # no signal
    assert J.authority(ledger, "coin", "k") == "shadow"
    _fill(ledger, "overconf", lambda y, r: 0.99 if y else 0.97, 400, seed=2)   # right ordering, awful calibration
    assert J.authority(ledger, "overconf", "k") == "shadow"
    _fill(ledger, "oracle", lambda y, r: 0.95 if y else 0.03, 300, seed=4)     # informative AND calibrated
    assert J.authority(ledger, "oracle", "k") == "advisory"
    assert J.authority(ledger, "oracle", "k", never_delegate=("k",)) == "advisory"
    _fill(ledger, "oracle", lambda y, r: 0.95 if y else 0.03, 800, seed=3)     # plenty of evidence
    assert J.authority(ledger, "oracle", "k") == "delegated"
    assert J.authority(ledger, "oracle", "k", never_delegate=("k",)) == "advisory"   # E0/E7-type decisions stay capped


def test_jev_client_parses_plausible_shapes_and_keeps_raw():
    posted = {}
    def post(body):
        posted.update(body)
        return {"answers": {"top20": {"choice": "true", "confidence": 0.8, "probabilities": {"true": 0.8, "false": 0.2}}}}
    cl = J.JevClient(post=post)
    ans, raw = cl.ask({"x": 1}, {"top20": {"type": "noul"}})
    assert ans["top20"]["probs"]["true"] == 0.8 and posted["questions"]["top20"]["type"] == "noul" and "answers" in raw
    alt = J.JevClient(post=lambda b: [{"choice": "a", "probs": {"a": 0.6, "b": 0.4}}]).ask({}, {"q": {"type": "choice"}})[0]
    assert alt["q"]["probs"]["a"] == 0.6
    weird = J.JevClient(post=lambda b: {"unexpected": 1}).ask({}, {"q": {"type": "choice"}})[0]
    assert weird["q"]["probs"] is None                                         # surprise => no crash, no invented numbers


def test_triage_is_shadow_until_calibrated_and_survives_outage(ledger, make_contract):
    from antslab.proposers import Proposal
    class Fake:
        name = "fake"
        def ask(self, state, questions):
            return {"top20": {"choice": "true", "probs": {"true": 0.9 if state["candidate"]["a"] < 0.3 else 0.1}}}, "{}"
    c = make_contract()
    tri = J.ProposalTriage(ledger, Fake(), "t", "s")
    props = [Proposal({"a": 0.9, "b": 3, "c": "u"}, "llm:A"), Proposal({"a": 0.1, "b": 3, "c": "u"}, "llm:A")]
    out = tri.score(props, "none", lambda s: f"k{s['a']}")
    assert out == props                                                         # shadow: order unchanged, predictions logged
    assert len(ledger.judgements("fake", tri.kind)) == 2
    class Down:
        name = "down"
        def ask(self, *a): raise TimeoutError()
    assert J.ProposalTriage(ledger, Down(), "t", "s").score(props, "", lambda s: "k") == props


def test_resolution_attaches_realised_outcomes(ledger, make_contract):
    c = make_contract()
    h = History(ledger, c, "h")
    for i, v in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
        ledger.enqueue("toy", "h", i, {"a": .1 * i, "b": 3, "c": "u"}, "x", spec_key=f"s{i}")
        t = ledger.claim(60, arm="h"); ledger.finish(t["id"], 1, "done", {"val_mse": v, "n_params": 10})
    h.update()
    ledger.log_judgement("j", "proposal_top20", "s0", 0.9, "yes", "{}", "shadow")
    ledger.log_judgement("j", "proposal_top20", "s4", 0.9, "yes", "{}", "shadow")
    assert J.resolve_triage(ledger, h, "j") == 2
    outs = {r["subject"]: r["outcome"] for r in ledger.judgements("j", "proposal_top20")}
    assert outs == {"s0": 1, "s4": 0}
