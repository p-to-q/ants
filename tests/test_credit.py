"""Credit assignment (credit.py): direction signs, multi-lever distribution, weights and report."""
import pytest


def _finish(ledger, gym, arm, i, spec, value, origin_key=None, edit=None, stage="s0", seed=None):
    ledger.enqueue(gym, arm, seed if seed is not None else i, spec, "evo", stage=stage,
                   spec_key=f"t{i}", origin_key=origin_key, edit=edit)
    t = ledger.claim(60, arm=arm)
    ledger.finish(t["id"], 1, "done", {"val_mse": value, "n_params": 50})
    return t["id"]


def test_lever_credit_signs_follow_direction(ledger, make_contract):
    """direction=min: a child better than its parent (cv < pv) is a win (d > 0);
    a worse child credits gain 0.0."""
    from antslab.credit import lever_credit
    c = make_contract()
    _finish(ledger, "toy", "h", 0, {"a": 0.5, "b": 5, "c": "u"}, 2.0)             # parent: val_mse 2.0
    _finish(ledger, "toy", "h", 1, {"a": 0.1, "b": 5, "c": "u"}, 1.0,
            origin_key="t0", edit=["a"])                                          # better child → win
    _finish(ledger, "toy", "h", 2, {"a": 0.9, "b": 5, "c": "u"}, 3.0,
            origin_key="t0", edit=["a"])                                          # worse child → no gain
    st = lever_credit(ledger, c, "h")
    assert st["a"]["n"] == 2 and st["a"]["wins"] == 1
    assert st["a"]["gain"] == pytest.approx(0.5, abs=1e-6)                        # d=0.5 (improvement), not clamped


def test_multi_lever_credit_is_distributed_over_edits(ledger, make_contract):
    """A child that changes k levers splits the credit: each lever gains d/k."""
    from antslab.credit import lever_credit
    c = make_contract()
    _finish(ledger, "toy", "h", 0, {"a": 0.5, "b": 5, "c": "u"}, 2.0)
    _finish(ledger, "toy", "h", 1, {"a": 0.1, "b": 8, "c": "u"}, 1.0,
            origin_key="t0", edit=["a", "b"])                                     # d = 0.5, split over 2 levers
    st = lever_credit(ledger, c, "h")
    assert set(st) == {"a", "b"}
    assert st["a"]["gain"] == pytest.approx(0.25, abs=1e-6)
    assert st["b"]["gain"] == pytest.approx(0.25, abs=1e-6)
    assert st["a"]["wins"] == 1 and st["b"]["wins"] == 1


def test_lever_weights_and_credit_report(ledger, make_contract):
    """Weights keep the exploration floor for untouched levers; the report is sorted by mean gain."""
    from antslab.credit import lever_weights, credit_report
    c = make_contract()
    _finish(ledger, "toy", "h", 0, {"a": 0.5, "b": 5, "c": "u"}, 2.0)
    _finish(ledger, "toy", "h", 1, {"a": 0.1, "b": 5, "c": "u"}, 1.0, origin_key="t0", edit=["a"])
    w = lever_weights(ledger, c, "h")
    assert set(w) == set(c.space.params)
    assert w["a"] > w["b"] and w["b"] >= 0.02                                     # touched lever wins, floor kept
    rep = credit_report(ledger, c, "h")
    assert [r["lever"] for r in rep] == ["a"] and rep[0]["tries"] == 1