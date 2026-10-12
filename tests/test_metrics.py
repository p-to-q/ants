import numpy as np
import pytest
from antslab import metrics as M


def test_geometric_aggregation_is_symmetric_and_scale_free():
    assert M.gmre([1.1, 1 / 1.1]) == pytest.approx(1.0)               # +10% and -10% cancel (arithmetic mean would not)
    assert M.gmre([0.5, 0.5, 2.0]) == pytest.approx(0.5 ** (2 / 3) * 2 ** (1 / 3))
    w = M.family_weights(["ETT", "ETT", "ETT", "ETT", "weather"])      # 4 correlated datasets vs 1 independent one
    assert sum(w[:4]) == pytest.approx(w[4]) == pytest.approx(1.0)


def test_seasonal_fidelity_separates_rhythm_from_mean_forecast():
    t = np.arange(96)
    true = np.stack([np.sin(2 * np.pi * t / 24 + ph) for ph in np.linspace(0, 3, 20)])
    assert M.seasonal_fidelity(true, true, [24]) < 1e-6
    assert M.seasonal_fidelity(np.zeros_like(true), true, [24]) > 0.4          # flat forecast: amplitude lost
    shifted = np.stack([np.sin(2 * np.pi * (t + 12) / 24 + ph) for ph in np.linspace(0, 3, 20)])
    assert M.seasonal_fidelity(shifted, true, [24]) > 0.4                      # right amplitude, opposite phase
    assert np.isnan(M.seasonal_fidelity(true, true, [168]))                    # period longer than horizon: not applicable


def test_perturbations_leave_shape_and_are_deterministic():
    X = np.random.default_rng(0).normal(size=(50, 96))
    for k in M.PERTURBATIONS:
        a, b = M.perturb(X, k, np.random.default_rng(1)), M.perturb(X, k, np.random.default_rng(1))
        assert a.shape == X.shape and np.array_equal(a, b) and not np.array_equal(a, X)
    assert M.robustness_index(1.0, [1.0, 1.0]) == pytest.approx(1.0) and M.robustness_index(1.0, [2.0, 2.0]) == pytest.approx(2.0)


def test_block_bootstrap_detects_real_gap_and_not_noise():
    rng = np.random.default_rng(0)
    base = rng.gamma(2.0, 1.0, 600)
    better = base * 0.9 + rng.normal(0, 0.02, 600)
    assert M.block_bootstrap_pimprove(better, base)["p_improve"] > 0.95
    other = base + rng.normal(0, 0.3, 600)
    r = M.block_bootstrap_pimprove(other, base)
    assert 0.02 < r["p_improve"] < 0.98 and r["ci90"][0] < 0 < r["ci90"][1]


def test_block_bootstrap_respects_autocorrelation():
    """iid resampling would be over-confident on autocorrelated loss differences; blocks must widen the interval."""
    rng = np.random.default_rng(0)
    e = np.zeros(800)
    for i in range(1, 800):
        e[i] = 0.95 * e[i - 1] + rng.normal()                           # strongly autocorrelated differential
    a, b = np.ones(800), np.ones(800) + 0.0
    b = b + e * 0.1
    wide = M.block_bootstrap_pimprove(a, b, block=50)["ci90"]
    narrow = M.block_bootstrap_pimprove(a, b, block=1)["ci90"]
    assert (wide[1] - wide[0]) > 1.5 * (narrow[1] - narrow[0])


def test_diebold_mariano_sign_and_calibration():
    rng = np.random.default_rng(1)
    a, b = rng.gamma(2, 1, 500), rng.gamma(2, 1, 500)
    assert M.diebold_mariano(a * 0.8, a, h=4)["stat"] > 3 and M.diebold_mariano(a * 0.8, a, h=4)["p"] < 0.01
    ps = [M.diebold_mariano(rng.gamma(2, 1, 300), rng.gamma(2, 1, 300), h=1)["p"] for _ in range(300)]
    assert 0.02 < np.mean(np.array(ps) < 0.05) < 0.10                    # ~5% false positives under the null


def test_indistinguishable_set_is_calibrated_over_replicates():
    """A tie must usually stay in the superior set and a clear loser must essentially never; measured, not assumed."""
    kept_tie = dropped_bad = 0
    reps = 100
    for seed in range(reps):
        rng = np.random.default_rng(seed)
        base = rng.gamma(2, 1, 400)
        losses = {"best": base, "tie": base + rng.normal(0, 0.05, 400), "bad": base * 1.4}
        keep = M.indistinguishable_from_best(losses)
        kept_tie += ("best" in keep) and ("tie" in keep)
        dropped_bad += "bad" not in keep
    assert kept_tie >= 0.88 * reps and dropped_bad >= 0.99 * reps


def test_pareto_frontier_area_and_ratings():
    pts = [(1.0, 0.9), (2.0, 0.7), (2.5, 0.8), (3.0, 0.6)]
    assert M.pareto_front(pts) == [0, 1, 3]
    a = M.frontier_area(pts, (4.0, 1.0))
    assert a > M.frontier_area([(1.0, 0.95), (2.0, 0.8), (3.0, 0.7)], (4.0, 1.0)) > 0     # better frontier => larger area
    r = M.bradley_terry([("A", "B")] * 8 + [("B", "A")] * 2 + [("B", "C")] * 7 + [("C", "B")] * 3)
    assert r["A"] > r["B"] > r["C"]
