"""Red team: metric gaming, degenerate inputs, negative controls and the statistics behind promotion."""
import math

import numpy as np
import pytest

from antslab import metrics as M
from antslab import synth_suite as S
from antslab.gyms import tinyts as T
from antslab.registry import Registry, decide_promotion

from . import _rt as rt
from .data_required import needs_raw
from .test_synth_suite import fit_eval


# ---- gmre ----------------------------------------------------------------------------------------------------------------
def test_gmre_refuses_inputs_that_could_manufacture_a_headline():
    for bad in ([], [float("nan"), 1.0], [-1.0, 2.0]):
        with pytest.raises(ValueError):
            M.gmre(bad)
    for w in ([0, 0], [1, -1], [1], [float("nan"), 1]):
        with pytest.raises(ValueError):
            M.gmre([1.0, 2.0], w)


def test_one_degenerate_task_cannot_drive_the_headline_to_zero_or_infinity():
    honest = [0.9] * 7
    assert M.gmre(honest + [0.0]) == pytest.approx(math.exp((7 * math.log(0.9) + math.log(M.RATIO_FLOOR)) / 8))   # a perfect fit counts as the floor
    assert math.isfinite(M.gmre(honest + [float("inf")]))                            # zero-error reference: capped, finite
    assert M.gmre(honest + [1e300]) == M.gmre(honest + [float("inf")]) < 1e4
    assert M.gmre([0.0]) == pytest.approx(M.RATIO_FLOOR) and M.gmre([float("inf")]) == pytest.approx(M.RATIO_CAP)
    assert M.gmre([1.1, 1 / 1.1]) == pytest.approx(1.0)                            # ordinary behaviour is untouched


# ---- paired statistics ---------------------------------------------------------------------------------------------------
def test_bootstrap_with_ties_constants_and_tiny_samples():
    same = np.ones(50)
    r = M.block_bootstrap_pimprove(same, same)
    assert r["p_improve"] == 0.0 and r["rel_improvement"] == 0.0 and r["p_value"] == 1.0          # a clone never 'improves'
    r = M.block_bootstrap_pimprove(same * 0.9, same)                                              # constant differential
    assert r["p_improve"] == 1.0 and r["p_value"] == 0.0 and r["rel_improvement"] == pytest.approx(0.1)
    assert M.block_bootstrap_pimprove(np.array([1.0, 1.0]), np.array([2.0, 2.0]))["n"] == 2
    with pytest.raises(ValueError, match="at least 2"):
        M.block_bootstrap_pimprove(np.array([1.0]), np.array([2.0]))                               # n=1 used to give P(improve)=1.0
    with pytest.raises(ValueError):
        M.block_bootstrap_pimprove(np.array([]), np.array([]))


def test_bootstrap_and_dm_refuse_nan_inf_and_mismatched_losses():
    good = np.ones(30)
    for bad in (np.r_[good[:-1], np.nan], np.r_[good[:-1], np.inf]):
        with pytest.raises(ValueError, match="non-finite"):
            M.block_bootstrap_pimprove(bad, good)
        with pytest.raises(ValueError, match="non-finite"):
            M.diebold_mariano(good, bad)
    with pytest.raises(ValueError, match="equal length"):
        M.block_bootstrap_pimprove(good, good[:20])
    with pytest.raises(ValueError, match="equal length"):
        M.diebold_mariano(good, good[:20])


def test_diebold_mariano_degenerate_cases_say_no_evidence():
    a = np.ones(40)
    assert M.diebold_mariano(a, a)["p"] == 1.0                                       # all ties
    assert M.diebold_mariano(a, a * 2)["p"] == 1.0                                   # constant differential: variance 0, no test
    assert M.diebold_mariano(a[:5], a[:5] * 2) == {"stat": 0.0, "p": 1.0, "n": 5}    # tiny n
    rng = np.random.default_rng(0)
    x = rng.gamma(2, 1, 200)
    r = M.diebold_mariano(x * 0.8, x + rng.normal(0, 0.01, 200), h=250)                 # horizon >> n/2: the HLN factor must stay real
    assert math.isfinite(r["stat"]) and 0.0 <= r["p"] <= 1.0


def test_p_value_is_calibrated_under_the_null_and_matches_a_known_t_quantile():
    assert M.student_t_sf(2.228138852, 10) == pytest.approx(0.025, abs=1e-8)
    assert M.student_t_sf(0.0, 7) == 0.5 and M.student_t_sf(3.0, 2) == pytest.approx(0.5 * (1 - 3 / math.sqrt(11)))
    rng = np.random.default_rng(3)
    ps = np.array([M.block_bootstrap_pimprove(rng.normal(0, 1, 240) + 5, rng.normal(0, 1, 240) + 5, block=12)["p_value"] for _ in range(300)])
    assert (ps < 0.05).mean() < 0.09 and (ps < 0.01).mean() < 0.03                   # not anti-conservative under H0
    gain = M.block_bootstrap_pimprove(np.full(240, 0.9) + rng.normal(0, .1, 240), np.ones(240) + rng.normal(0, .1, 240))
    assert gain["p_value"] < 1e-6


# ---- decide_promotion fails closed ------------------------------------------------------------------------------------------
def _pair(n=100, fams=("A", "B", "C"), gain=0.2, seed=0):
    rng = np.random.default_rng(seed)
    inc = {f: rng.gamma(2, 1, n) for f in fams}
    return {f: v * (1 - gain) for f, v in inc.items()}, inc


def test_a_challenger_cannot_cherry_pick_the_families_it_wins_on():
    cand, inc = _pair()
    assert decide_promotion(cand, inc)["verdict"] == "accepted"
    only_a = {"A": cand["A"]}
    d = decide_promotion(only_a, inc)                                                # used to be 'accepted' (need = min(2, 1))
    assert d["verdict"] == "failed" and "family sets differ" in d["reason"]
    d = decide_promotion(only_a, {"A": inc["A"]})                                    # one family in total: not 'independent support'
    assert d["verdict"] == "unstable" and any("need 2" in r for r in d["reasons"])


def test_malformed_loss_vectors_are_failed_not_judged():
    cand, inc = _pair()
    bad = {**cand, "B": cand["B"][:50]}
    assert decide_promotion(bad, inc)["verdict"] == "failed"                         # used to raise a broadcasting error
    nan = {**cand, "B": np.where(np.arange(100) == 5, np.nan, cand["B"])}
    d = decide_promotion(nan, inc)
    assert d["verdict"] == "failed" and "non-finite" in d["reason"]                  # used to be a terminal 'rejected'
    assert decide_promotion({f: -np.ones(100) for f in inc}, inc)["verdict"] == "failed"         # negative losses used to win
    assert decide_promotion(cand, {f: np.zeros(100) for f in inc})["verdict"] == "failed"
    assert decide_promotion({f: v[:10] for f, v in cand.items()}, {f: v[:10] for f, v in inc.items()})["verdict"] == "failed"   # 10 windows
    assert decide_promotion({}, inc)["verdict"] == "failed"


def test_a_failed_challenge_leaves_the_model_where_it_was(ledger):
    reg = Registry(ledger)
    reg.register_baseline("naive", "g", 100, {"x": 0}, "k0")
    m = reg.add("m", "g", 100, {"x": 1}, "k1"); assert rt.confirm(reg, m)["passed"]
    base = rt.base_losses()
    inc = rt.record(reg, reg.incumbent("g", 100)["id"], base, 1.0)
    cand = rt.record(reg, m, base, 0.9)
    cand["losses"]["ETT"][3] = float("nan")                                          # a single poisoned window
    assert reg.challenge(m, cand, inc)["verdict"] == "failed"
    assert reg.get(m)["status"] == "confirmed"                                       # retry with clean evidence is still possible


# ---- online FDR -------------------------------------------------------------------------------------------------------------
def test_lord_levels_are_a_valid_spending_sequence():
    assert sum(M.lord_gamma(t) for t in range(1, 200_000)) < 1.0 and M.lord_gamma(0) == 0.0 and M.lord_gamma(-3) == 0.0
    lv = [M.lord_level(t, []) for t in range(1, 50)]
    assert all(a > b > 0 for a, b in zip(lv, lv[1:])) and lv[0] == pytest.approx(M.lord_gamma(1) * 0.025)
    assert M.lord_level(10, [4]) > M.lord_level(10, [])                               # a past discovery earns testing power
    assert M.lord_level(10, [4, 6]) > M.lord_level(10, [4])
    with pytest.raises(ValueError):
        M.lord_level(3, [3])                                                          # rejections must precede the test
    with pytest.raises(ValueError):
        M.lord_level(3, [], alpha=0.05, w0=0.2)


def test_lord_controls_fdr_on_independent_uniform_nulls_with_an_occasional_signal():
    """Pure math check of the procedure: 400 streams of 100 tests; 10% true signals (p ~ 1e-6), the rest uniform."""
    rng = np.random.default_rng(7)
    fdp = []
    for _ in range(400):
        rej, false = [], 0
        for t in range(1, 101):
            signal = rng.random() < 0.1
            p = 10 ** rng.uniform(-8, -5) if signal else rng.random()
            if p <= M.lord_level(t, rej):
                rej.append(t); false += not signal
        fdp.append(false / max(1, len(rej)))
    assert np.mean(fdp) <= 0.05


# ---- negative controls and trivial forecasters ---------------------------------------------------------------------------------
def test_trivial_forecasters_and_the_skill_gate_on_known_cases():
    rng = np.random.default_rng(0)
    X, Y = rng.normal(size=(400, 48)), rng.normal(size=(400, 24))
    t = M.trivial_mses(X, Y, 24)
    assert set(t) == {"last", "window_mean", "zero", "snaive"} and t["last"] > 1.7 and 0.9 < t["zero"] < 1.1
    assert M.beats_trivial(0.5 * t["zero"], X, Y, 24)["ok"] is True
    g = M.beats_trivial(1.0 * t["zero"], X, Y, 24)
    assert g["ok"] is False and g["best_trivial_name"] == min(t, key=t.get)
    assert M.beats_trivial(float("nan"), X, Y)["ok"] is False


def test_rel_mse_below_one_is_NOT_evidence_of_skill_on_a_negative_control():
    """REPRODUCED: the headline is relative to the seasonal naive. On white noise the 'forecast the mean' spec scores ~0.5 and on a
    random walk the 'last value' spec ~0.8: both look like wins and carry no information. Skill must be judged against the best trivial
    forecaster (metrics.beats_trivial)."""
    base = T.baseline_specs()["period_linear"]
    mean = {**base, "core": "linear", "period": 1, "norm": "mean", "ridge": 1e9, "lookback": 48}
    last = {**base, "core": "linear", "period": 1, "norm": "last", "ridge": 1e9, "lookback": 48}
    wn = T.evaluate_catalog(mean, ["syn_white_noise_1"], "val", [96], 6000, 3000)["rel_mse"]
    rw = T.evaluate_catalog(last, ["syn_random_walk_1"], "val", [96], 6000, 3000)["rel_mse"]
    assert wn < 0.6 and rw < 0.9


@pytest.mark.parametrize("name", ["white_noise", "random_walk", pytest.param("shuffled_etth1", marks=needs_raw)])
def test_no_spec_beats_the_best_trivial_forecaster_on_a_negative_control(name):
    if name == "shuffled_etth1":
        from antslab.data import Catalog, locate_catalog
        base = Catalog(locate_catalog()).load("etth1").values[:20000, :8]
        x = S.REAL_BASED["shuffled"](base, np.random.default_rng(1))
    else:
        x, _ = S.generate(name, 1)
    specs = [dict(core="linear", period=1, norm="none", ridge=1.0), dict(core="linear", period=1, norm="last", ridge=1.0),
             dict(core="period", period=24, norm="revin", ridge=1.0), dict(core="linear", period=1, norm="none", ridge=1e-6),
             dict(core="fourier", period=1, norm="mean", ridge=1.0)]
    for kw in specs:
        for L in (96, 336):
            mse, d, Xv, Yv = fit_eval(x, L=L, H=96, spec_kw={**kw, "lookback": L})
            g = M.beats_trivial(mse, Xv, Yv, 24, margin=0.0)
            assert not g["ok"] and mse >= 0.99 * g["best_trivial"], (name, kw, L, g)


def test_a_real_signal_does_pass_the_skill_gate_so_the_gate_is_not_vacuous():
    x, _ = S.generate("sines_ar", 1)
    mse, d, Xv, Yv = fit_eval(x, L=336, H=96, spec_kw={"core": "linear", "period": 1, "norm": "none", "lookback": 336})
    assert M.beats_trivial(mse, Xv, Yv, 24, margin=0.05)["ok"] is True


@needs_raw
def test_degenerate_specs_do_not_pass_the_quality_gates():
    base = T.baseline_specs()["period_linear"]
    degenerate = {
        "last_value": {**base, "core": "linear", "period": 1, "norm": "last", "ridge": 1e9, "lookback": 48},
        "window_mean": {**base, "core": "linear", "period": 1, "norm": "mean", "ridge": 1e9, "lookback": 48},
        "constant_zero": {**base, "core": "linear", "period": 1, "norm": "none", "ridge": 1e9, "lookback": 48},
        "seasonal_naive": {**base, "snaive": True, "ridge": 1e9},
    }
    results = []
    for name, spec in degenerate.items():
        m = T.evaluate_stage_catalog(spec, "s0")
        results.append((name, m["rel_mse"]))
        assert m["rel_mse"] >= 1.0 - 1e-6, (name, m["rel_mse"])                      # none of them is a genuine win on real dev data
    assert dict(results)["seasonal_naive"] < 1.0                                  # ...but the reference itself is 1 - 5e-10 ("beats" 1.0)
    assert dict(results)["seasonal_naive"] > 1.0 - 1e-6


def test_the_reference_forecaster_itself_does_not_pass_the_confirm_gate(ledger):
    reg = Registry(ledger)
    m = reg.add("snaive", "tinyts", 1000, {"x": 1}, "k")
    metrics = {"rel_mse": 0.9999999995, "n_params": 420}                              # what the seasonal-naive spec scores
    r = reg.confirm(m, metrics, dict(metrics))
    assert not r["passed"] and any("reference" in f for f in r["fails"])
