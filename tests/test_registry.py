import numpy as np
import pytest
from antslab.registry import Registry, decide_promotion
from . import _rt as rt


def losses(rng, scale, fams=("ETT", "weather", "ecl"), n=400):
    return {f: rng.gamma(2, 1, n) * scale for f in fams}


def _pair(gain, seed=0, fams=("ETT", "weather", "ecl"), noise=0.0):
    rng = np.random.default_rng(seed)
    inc = {f: rng.gamma(2, 1, 500) for f in fams}
    cand = {f: inc[f] * (1 - gain) + rng.normal(0, noise, 500) for f in fams}
    return cand, inc


def test_accepts_consistent_gain_across_families():
    cand, inc = _pair(0.03)
    d = decide_promotion(cand, inc)
    assert d["verdict"] == "accepted" and len(d["improved_families"]) == 3


def test_single_family_gain_is_unstable_not_accepted():
    cand, inc = _pair(0.0)
    cand["ETT"] = inc["ETT"] * 0.9
    d = decide_promotion(cand, inc)
    assert d["verdict"] == "unstable" and any("independent families" in r for r in d["reasons"])


def test_gain_below_practical_threshold_is_not_accepted():
    cand, inc = _pair(0.001)
    assert decide_promotion(cand, inc)["verdict"] in ("unstable", "rejected")


def test_regression_in_one_family_blocks_promotion():
    cand, inc = _pair(0.05)
    cand["ecl"] = inc["ecl"] * 1.15
    d = decide_promotion(cand, inc)
    assert d["verdict"] != "accepted" and "ecl" in d["worse_families"]


def test_mse_gain_with_worse_mae_is_not_accepted():
    cand, inc = _pair(0.03)
    assert decide_promotion(cand, inc, mae_ratio=1.02)["verdict"] != "accepted"


def test_no_common_windows_fails_closed():
    assert decide_promotion({"a": np.ones(5)}, {"b": np.ones(5)})["verdict"] == "failed"


def test_registry_ladder_and_single_champion_per_bin(ledger):
    reg = Registry(ledger)
    base = rt.base_losses()
    n0 = reg.register_baseline("naive", "tinyts", 300, {"x": 0}, "k0")
    a = reg.add("a", "tinyts", 300, {"x": 1}, "ka"); b = reg.add("b", "tinyts", 300, {"x": 2}, "kb")
    with pytest.raises(ValueError):
        reg.transition(a, "champion")                                   # cannot skip the ladder
    rt.climb(reg, ledger, a, 0.95, base, 1.0)
    assert reg.champion("tinyts", 300)["name"] == "a"
    rt.climb(reg, ledger, b, 0.90, base, 0.95)                          # b must beat the champion, a
    assert reg.champion("tinyts", 300)["name"] == "b" and reg.get(a)["status"] == "retired"
    assert ledger.verify_chain()[0] and len([e for e in ledger.events("model_status")]) >= 7
    assert reg.get(n0)["status"] == "validated" and reg.verify() == []


def test_challenge_uses_paired_test_and_records_evidence(ledger):
    reg = Registry(ledger)
    reg.register_baseline("naive", "tinyts", 100, {"x": 0}, "k0")
    m = reg.add("m", "tinyts", 100, {"x": 1}, "k")
    base = rt.base_losses(3)
    d = rt.validate(reg, m, 1.0, base, 1.0)                              # identical losses: no gain
    assert d["verdict"] in ("rejected", "unstable")
    assert reg.get(m)["status"] in ("rejected", "unstable")
    ev = ledger.events("model_status")[-1]["payload"]["evidence"]
    assert "verdict" in ev and "pooled" in ev
