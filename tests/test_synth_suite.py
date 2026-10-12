import numpy as np
import pytest
from pathlib import Path
from .data_required import needs_raw
from antslab import synth_suite as S
from antslab.data import Catalog, sample_windows
from antslab.gyms import tinyts as T

CAT = Path(__file__).resolve().parents[1] / "data" / "catalog.toml"


def fit_eval(x, L=96, H=96, spec_kw=None, tr_cap=6000, va_cap=3000):
    """The REAL window sampling + REAL model, standardised with train statistics (as the gym does)."""
    n_tr, n_va = 8640, 2880
    mu, sd = x[:n_tr].mean(0), x[:n_tr].std(0) + 1e-8
    d = (x - mu) / sd
    spec = {**T.baseline_specs()["period_linear"], "core": "linear", "period": 1, "lookback": L, "norm": "none", "ridge": 1.0, **(spec_kw or {})}
    Xt, Yt = sample_windows(d, 0, n_tr, L, H, tr_cap, 1)
    Xv, Yv = sample_windows(d, n_tr, n_tr + n_va, L, H, va_cap, 2)
    m = T.Model(spec).fit(Xt, Yt)
    return float(np.mean((m.predict(Xv, H) - Yv) ** 2)), d, Xv, Yv


def test_every_generator_is_deterministic_and_seed_sensitive():
    for kind, fams in S.GENERATORS.items():
        for f in fams:
            a, _ = S.generate(f, 3); b, _ = S.generate(f, 3); c, _ = S.generate(f, 4)
            assert np.array_equal(a, b, equal_nan=True) and not np.array_equal(a, c, equal_nan=True), f
            assert a.ndim == 2 and a.shape[0] >= 600


def test_no_model_beats_the_oracle_noise_floor():
    """Known answer: standardised MSE on snr=S data cannot go below 1/(1+S). Beating it means the pipeline leaks."""
    for level in (100.0, 1.0):
        x, info = S.generate(f"snr{int(level)}" if level >= 1 else "snr0.1", 5)
        floor = info["oracle_mse_floor_standardised"]
        best = min(fit_eval(x, L, 96)[0] for L in (96, 336))
        assert best >= 0.9 * floor, (level, best, floor)
        assert best <= floor + 0.5 + (0.05 if level == 100 else 0.0)      # and it should get reasonably near it


def test_negative_controls_show_no_skill_through_the_real_pipeline():
    x, _ = S.generate("white_noise", 1)
    mse, d, Xv, Yv = fit_eval(x)
    assert mse > 0.97                                     # variance ~1: nothing to learn; < 1 by a margin would be leakage
    naive = np.mean((Xv[:, -1:] - Yv) ** 2)               # last-value forecast of noise has MSE ~ 2
    assert 1.8 < naive < 2.2 and mse < naive
    x, _ = S.generate("random_walk", 1)
    mse, d, Xv, Yv = fit_eval(x, spec_kw={"norm": "last"})
    naive = np.mean((Xv[:, -1:] - Yv) ** 2)
    assert mse > 0.9 * naive                              # cannot beat the last-value forecast of a random walk


def test_extreme_scale_is_neutralised_by_standardisation():
    a, _ = S.generate("scale_huge", 1); b, _ = S.generate("sines_ar", 1)
    za = (a - a[:8640].mean(0)) / a[:8640].std(0); zb = (b - b[:8640].mean(0)) / b[:8640].std(0)
    assert np.allclose(za, zb, atol=1e-6)
    t, _ = S.generate("scale_tiny", 1)
    zt = (t - t[:8640].mean(0)) / t[:8640].std(0)
    assert np.allclose(zt, zb, atol=1e-6)


def test_hostile_inputs_never_produce_non_finite_forecasts():
    for fam in ("constant_channels", "outlier_burst", "quantised", "exp_growth", "sign_chaos", "shift_end"):
        x, _ = S.generate(fam, 1)
        mse, *_ = fit_eval(x)
        assert np.isfinite(mse), fam


def test_chaotic_series_are_bounded_and_deterministic_but_not_periodic():
    x, info = S.generate("lorenz", 2)
    assert info["deterministic_chaos"] and np.abs(x).max() < 80 and x.shape[1] == 3
    mg, _ = S.generate("mackey_glass", 2)
    assert 0 < mg.min() and mg.max() < 2.0


def test_catalog_has_the_synthetic_suite_with_stress_role_and_disjoint_families():
    cat = Catalog(CAT)
    syn = [n for n in cat.names() if n.startswith("syn_")]
    assert len(syn) >= 40 and all(cat.meta[n]["role"] == "stress" for n in syn)
    real = {cat.meta[n]["family"] for n in cat.names() if not n.startswith("syn_")}
    assert not ({cat.meta[n]["family"] for n in syn} & real)
    s = cat.load("syn_missing_blocks_1")
    assert not np.isnan(s.values).any()                    # NaN blocks were forward-filled by the unified loader


NEW = ["arima", "sarima", "structural", "garch", "hawkes", "ou_process", "weekday_calendar", "random_rnn", "random_nonlinear_ar",
       "kernel_synth", "hybrid", "rossler", "van_der_pol", "lotka_volterra", "henon_logistic", "all_constant", "piecewise_constant",
       "saturated", "onoff_machine", "staircase_trend"]


def test_new_generators_are_finite_deterministic_and_stable():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")                                   # an overflow/invalid warning is a bug, not noise
        for f in NEW:
            a, _ = S.generate(f, 7); b, _ = S.generate(f, 7)
            assert np.isfinite(a).all() and np.array_equal(a, b), f
            assert np.abs(a).max() < 1e7, f                               # no explosive dynamics


def test_degenerate_families_have_the_advertised_structure():
    c, _ = S.generate("all_constant", 1)
    assert (np.ptp(c, axis=0) == 0).all()                                  # exactly one value per channel
    p, _ = S.generate("piecewise_constant", 1)
    assert (np.abs(np.diff(p, axis=0)) < 0.2).mean() > 0.97               # almost always flat
    sat, _ = S.generate("saturated", 1)
    assert np.mean([(sat[:, j] == sat[:, j].max()).mean() + (sat[:, j] == sat[:, j].min()).mean() for j in range(sat.shape[1])]) > .3
    st, _ = S.generate("staircase_trend", 1)
    assert (np.diff(st, axis=0) >= -1e-12).all()                          # monotone: the single deliberately monotone family
    oo, _ = S.generate("onoff_machine", 1)
    assert 0.2 < (oo > 1.0).mean() < 0.8                                  # both states visited
    h, info = S.generate("hybrid", 1)
    assert len({tuple(m) for m in info["mixtures"]}) > 1                  # channels use DIFFERENT compositions: no single mechanism


def test_hybrid_and_kernel_synth_are_not_one_algorithm():
    k, info = S.generate("kernel_synth", 3)
    assert len(set(info["programs"])) > 1
    z = (k - k.mean(0)) / (k.std(0) + 1e-9)
    ac = [np.corrcoef(z[:-24, c], z[24:, c])[0, 1] for c in range(z.shape[1])]
    assert max(ac) - min(ac) > 0.05                                       # channels differ in temporal structure


@needs_raw
def test_real_based_surrogates_preserve_what_they_claim():
    from antslab.data import Catalog
    cat = Catalog(CAT)
    base = cat.load("etth1").values[:3000, :3]
    rng = np.random.default_rng(0)
    pr = S.phase_randomised(base, rng)
    assert np.allclose(np.abs(np.fft.rfft(pr[:, 0] - pr[:, 0].mean())), np.abs(np.fft.rfft(base[:, 0] - base[:, 0].mean())), rtol=1e-6, atol=1e-6)
    sh = S.shuffled(base, rng)
    assert np.allclose(np.sort(sh[:, 0]), np.sort(base[:, 0]))             # identical marginal ...
    ac = lambda x: np.corrcoef(x[:-1], x[1:])[0, 1]
    assert abs(ac(sh[:, 0])) < .1 < ac(base[:, 0])                         # ... but time structure is gone: a negative control
    bb = S.block_bootstrap(base, rng, 1000, 96)
    assert bb.shape == (1000, 3) and np.isin(bb[:5, 0], base[:, 0]).all()
    ars = S.ar_simulation(base, rng, 1500)
    assert ars.shape == (1500, 3) and np.isfinite(ars).all() and ac(ars[:, 0]) > .5


@needs_raw
def test_shuffled_real_data_is_a_negative_control_through_the_real_pipeline():
    from antslab.data import Catalog
    base = Catalog(CAT).load("etth1").values[:14400]
    x = S.shuffled(base, np.random.default_rng(3))
    mse, d, Xv, Yv = fit_eval(x)
    assert mse > 0.95                                                      # no skill can exist on a time-shuffled series
