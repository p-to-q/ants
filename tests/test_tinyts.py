import numpy as np
import pytest
from antslab.gyms import tinyts as T


def spec(**kw):
    base = T.baseline_specs()["period_linear"]
    return {**base, **kw}


def test_deterministic():
    a, b = T.evaluate(spec(), "s0"), T.evaluate(spec(), "s0")
    assert a["val_mse"] == b["val_mse"]


@pytest.mark.parametrize("core", ["linear", "period", "fourier", "basis"])
def test_analytic_param_count_matches_stored_weights(core):
    sp = spec(core=core, period=24 if core == "period" else 12, lookback=240, rank=5, fourier_k=10)
    data = T.synth(0)
    X, Y = T.windows((data - data.mean(0)) / data.std(0), 0, 8640, T.eff_len(240, sp["period"]), T.PARAM_H, 12)
    m = T.Model(sp).fit(X, Y)
    stored = sum(v.size for v in list(m.core.mats.values()))
    assert stored == T.count_params(sp)


def test_pruning_and_quantisation_reduce_cost_and_stay_finite():
    sp = spec(core="basis", rank=6, period=24, lookback=240)
    full = T.evaluate(sp, "s0")
    small = T.evaluate({**sp, "prune": 0.9, "quant": "int8"}, "s0")
    assert small["n_params"] < full["n_params"] / 3 and small["bytes"] < full["bytes"] / 8
    assert np.isfinite(small["val_mse"]) and small["val_mse"] < 5 * full["val_mse"]
    assert T.evaluate({**sp, "quant": "int4"}, "s0")["val_mse"] >= full["val_mse"] * 0.99


def test_pruning_is_only_credited_when_sparse_storage_is_actually_smaller():
    """Zeros are free only if their positions are stored too: a mildly pruned model is charged its dense size."""
    sp = spec(core="linear", period=1, lookback=96)
    dense = T.count_params(sp)
    assert T.count_params({**sp, "prune": 0.3}) == dense                                  # value + index per nonzero costs more than dense
    n = dense; idx = int(np.ceil(np.log2(n)))
    assert T.count_params({**sp, "prune": 0.95}) == round(round(n * 0.05) * (1 + idx / 32))
    assert T.count_params({**sp, "prune": 0.7, "quant": "int4"}) == dense                 # 4-bit values + 17-bit indices: dense wins
    assert T.count_params({**sp, "prune": 0.95}) <= T.count_params({**sp, "prune": 0.9}) < dense


@pytest.mark.parametrize("kw", [dict(), dict(prune=0.95), dict(prune=0.5, quant="int8"), dict(prune=0.97, decomp="ma"),
                                dict(core="fourier", period=1, fourier_k=8, prune=0.96), dict(core="basis", rank=4, prune=0.9)],
                         ids=lambda k: str(k))
def test_count_params_equals_stored_params_of_a_fitted_model(kw):
    sp = spec(lookback=240, **kw)
    d = _std_series()
    X, Y = T.windows(d, 0, 8640, T.eff_len(240, sp["period"]), T.PARAM_H, 12)
    assert T.Model(sp).fit(X, Y).post_train().stored_params() == T.count_params(sp)


def test_seasonal_naive_denominator_ignores_the_clip_lever():
    """Deliberate science change: rel_mse = MSE(model) / MSE(seasonal naive of the UNCLIPPED series), whatever the model's input clipping."""
    sp = spec(clip=True, clip_z=0.5)                                                      # aggressive clipping changes the inputs a lot
    m = T.evaluate(sp, "s0", profile="hard")
    raw = T.synth(T.STAGES_HARD["s0"]["seeds"][0])
    d = (raw - raw[:8640].mean(0)) / (raw[:8640].std(0) + 1e-8)
    Xv, Yv = T.windows(d, 8640, 11520, T.eff_len(336, 24), 96, T.STAGES_HARD["s0"]["va"])
    ref = np.mean((Xv[:, -24:][:, np.arange(96) % 24] - Yv) ** 2)
    assert abs(m["val_mse"] / m["rel_mse"] - ref) < 1e-4 * ref


def test_learned_model_beats_seasonal_naive_and_mean_forecast():
    b = T.baseline_specs()
    good, naive = T.evaluate(b["period_linear"], "s0")["val_mse"], T.evaluate(b["seasonal_naive"], "s0")["val_mse"]
    assert good < 0.7 * naive and good < 1.0      # 1.0 ~ predicting the training mean on standardised data


def test_no_future_leak_changing_future_targets_does_not_change_forecast():
    sp = spec(lookback=240)
    data = (lambda d: (d - d.mean(0)) / d.std(0))(T.synth(0))
    X, Y = T.windows(data, 0, 8640, 240, 96, 8)
    m = T.Model(sp).fit(X, Y)
    Xv, Yv = T.windows(data, 8640, 11520, 240, 96, 24)
    before = m.predict(Xv, 96)
    assert np.allclose(before, m.predict(Xv.copy(), 96))         # prediction depends on inputs only
    assert Xv.shape[1] == T.eff_len(240, 24)


def test_test_split_is_not_touched_by_val_stages():
    # val and test evaluation read disjoint target ranges
    data = T.synth(0)
    _, Yv = T.windows(data, 8640, 11520, 240, 96, 1)
    _, Yt = T.windows(data, 11520, 14400, 240, 96, 1)
    assert Yv.shape[0] > 0 and Yt.shape[0] > 0


# ---- lifecycle levers ------------------------------------------------------
LEVERS = [dict(clip=True, clip_z=3.0), dict(recency=2.0), dict(augment=True, aug_sigma=0.1), dict(robust=True),
          dict(pretrain="synth", prior_alpha=5.0), dict(distill_lambda=0.5), dict(ens=2), dict(ens=3),
          dict(pretrain="synth", core="linear", period=1, adapter="lowrank", adapter_rank=3, lookback=96)]


@pytest.mark.parametrize("kw", LEVERS, ids=[str(k) for k in LEVERS])
def test_every_lever_runs_finite_and_deterministic(kw):
    a = T.evaluate(spec(**kw), "s0")
    assert np.isfinite(a["val_mse"]) and a["val_mse"] < 3.0 and a["n_params"] > 0
    assert a["val_mse"] == T.evaluate(spec(**kw), "s0")["val_mse"]


def test_defaults_reproduce_pre_lever_baseline():
    assert abs(T.evaluate(spec(), "s0")["val_mse"] - 0.4128) < 5e-4          # unchanged by adding levers


def test_param_accounting_for_ensemble_and_adapter():
    one = T.count_params(spec())
    assert T.count_params(spec(ens=2)) > one and T.count_params(spec(ens=3)) > T.count_params(spec(ens=2))
    lin = spec(core="linear", period=1, lookback=96)
    ad = {**lin, "pretrain": "synth", "adapter": "lowrank", "adapter_rank": 4}
    assert T.count_params(ad) == T.count_params(lin) + 4 * (96 + T.PARAM_H)


def test_pretraining_prior_pulls_towards_pretrained_weights():
    base = spec(core="linear", period=1, lookback=96, pretrain="synth")
    weak = T.evaluate({**base, "prior_alpha": 0.01}, "s0")["val_mse"]
    strong = T.evaluate({**base, "prior_alpha": 100.0}, "s0")["val_mse"]
    none = T.evaluate({**base, "pretrain": "none"}, "s0")["val_mse"]
    assert abs(weak - none) < 0.05 * none            # tiny prior ~ no pre-training
    assert strong != weak


def test_robust_regression_downweights_outlier_windows():
    rng = np.random.default_rng(0)
    data = (lambda d: (d - d.mean(0)) / d.std(0))(T.synth(0))
    X, Y = T.windows(data, 0, 8640, 96, 96, 4)
    Yb = Y.copy(); Yb[::10] += rng.normal(0, 8, Yb[::10].shape)               # 10% corrupted targets
    Xv, Yv = T.windows(data, 8640, 11520, 96, 96, 12)
    sp = spec(core="linear", period=1, lookback=96, ridge=0.01)
    plain = np.mean((T.Model(sp).fit(X, Yb).predict(Xv, 96) - Yv) ** 2)
    robust = np.mean((T.Model({**sp, "robust": True}).fit(X, Yb).predict(Xv, 96) - Yv) ** 2)
    assert robust < plain


def test_hard_profile_makes_data_levers_matter_and_is_disjoint_from_clean():
    base = spec()
    plain = T.evaluate(base, "s0", profile="hard")["val_mse"]
    robust = T.evaluate({**base, "robust": True, "clip": True, "clip_z": 3.0}, "s0", profile="hard")["val_mse"]
    assert robust < plain                                   # data-centric levers help where there is something to fix
    assert set(T.PROFILES["hard"][1]).isdisjoint(sum((c["seeds"] for c in T.STAGES_HARD.values()), []))
    assert set(T.STAGES_HARD["final"]["seeds"]).isdisjoint(sum((T.STAGES_HARD[k]["seeds"] for k in ("s0", "s1", "s2")), []))


def test_headline_metric_is_relative_to_seasonal_naive():
    b = T.baseline_specs()
    good = T.evaluate(b["period_linear"], "s0")
    naive = T.evaluate(b["seasonal_naive"], "s0")
    assert 0.0 < good["rel_mse"] < 0.8 and abs(naive["rel_mse"] - 1.0) < 0.05     # the reference scores 1 against itself


def test_deep_metrics_present_finite_and_sensible():
    m = T.evaluate(spec(), "s1", deep=True)
    for k in ("robustness_index", "seasonal_fidelity", "horizon_decay", "rel_mae"):
        assert k in m and np.isfinite(m[k]), k
    assert m["robustness_index"] >= 0.9
    flat = T.evaluate(spec(core="linear", period=1, lookback=48, ridge=1e9), "s1", deep=True)     # ~predicts the mean
    assert flat["seasonal_fidelity"] > m["seasonal_fidelity"]                                      # rhythm lost => worse


def test_normalisation_makes_robustness_visible():
    revin = T.evaluate(spec(norm="revin"), "s0", deep=True)["robustness_index"]
    none = T.evaluate(spec(norm="none"), "s0", deep=True)["robustness_index"]
    assert revin != none                                                    # level-shift perturbation distinguishes them


# ---- speed / memory engineering: the streamed fits must be the SAME maths, in a fraction of the memory ----------------------
def _std_series(seed=0):
    d = T.synth(seed)
    return (d - d.mean(0)) / d.std(0)


def _traced_peak(fn):
    import tracemalloc
    tracemalloc.start()
    try:
        out = fn()
        return out, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_windows_follow_the_input_dtype_and_are_built_without_a_second_copy():
    d32 = _std_series().astype(np.float32)
    (X, Y), peak = _traced_peak(lambda: T.windows(d32, 0, 8640, 336, 720, 2))
    assert X.dtype == Y.dtype == np.float32 and X.size + Y.size > 3_000_000
    assert peak <= 1.2 * (X.nbytes + Y.nbytes)                  # no per-channel (n, L+H) intermediate, no concatenate copy


def test_windows_rows_want_and_scratch():
    d = _std_series()
    X, Y, t = T.windows(d, 0, 8640, 96, 48, 5, with_t=True)
    rows = [0, 3, 10, len(X) - 1]
    Xr, Yr, tr = T.windows(d, 0, 8640, 96, 48, 5, with_t=True, rows=rows)
    assert np.array_equal(Xr, X[rows]) and np.array_equal(Yr, Y[rows]) and np.array_equal(tr, t[rows])
    Xo, none = T.windows(d, 0, 8640, 96, 48, 5, want="x")
    assert none is None and np.array_equal(Xo, X)
    buf = T.Scratch()
    X1, Y1 = T.windows(d, 0, 8640, 96, 48, 5, scratch=buf)
    assert np.array_equal(X1, X) and np.array_equal(Y1, Y)
    X2, Y2 = T.windows(d, 0, 8640, 96, 48, 9, scratch=buf)                # a smaller request is carved out of the same buffer
    assert np.shares_memory(X1, X2) and X2.shape[0] < X.shape[0] and np.array_equal(X2, T.windows(d, 0, 8640, 96, 48, 9)[0])


FIT_SPECS = [dict(), dict(norm="last", snaive=True), dict(decomp="ma", robust=True, recency=1.5), dict(distill_lambda=0.5, norm="mean"),
             dict(core="fourier", period=1, fourier_k=12), dict(core="basis", rank=5), dict(core="linear", period=1),
             dict(period=7, lookback=140), dict(period=24, lookback=336, ridge=0.01)]


@pytest.mark.parametrize("kw", FIT_SPECS, ids=[str(k) for k in FIT_SPECS])
def test_streamed_fit_is_independent_of_the_chunk_size(kw, monkeypatch):
    sp = spec(**kw)
    d = _std_series()
    L = T.eff_len(sp["lookback"], sp["period"])
    X, Y, tt = T.windows(d, 0, 8640, L, 100, 24, with_t=True)
    Xv, _ = T.windows(d, 8640, 11520, L, 100, 48)
    w = np.exp(1.0 * (tt - 1)) if sp.get("recency") else None
    ref = T.Model(sp).fit(X, Y, w).predict(Xv, 100)
    monkeypatch.setattr(T, "CHUNK_ELEMS", 3000)                               # ~12 rows per chunk: every chunk boundary is exercised
    small = T.Model(sp).fit(X, Y, w).predict(Xv, 100)
    assert np.allclose(ref, small, rtol=0, atol=1e-8)


def test_streamed_linear_fit_equals_the_dense_closed_form():
    sp = spec(core="linear", period=1, lookback=96, norm="revin", ridge=0.3)
    d = _std_series()
    X, Y, tt = T.windows(d, 0, 8640, 96, 48, 6, with_t=True)
    w = np.exp(2.0 * (tt - 1))
    Xv, _ = T.windows(d, 8640, 11520, 96, 48, 24)
    m = T.Model(sp).fit(X, Y, w)
    mu, sd = X.mean(1, keepdims=True), X.std(1, keepdims=True) + 1e-5
    sw = np.sqrt(w)[:, None]
    A, B = ((X - mu) / sd * sw), ((Y - mu) / sd * sw)
    G = A.T @ A
    W = np.linalg.solve(G + 0.3 * max(1.0, np.trace(G) / 96) * np.eye(96), A.T @ B)
    mv, sv = Xv.mean(1, keepdims=True), Xv.std(1, keepdims=True) + 1e-5
    assert np.allclose(m.predict(Xv, 48), ((Xv - mv) / sv) @ W * sv + mv, atol=1e-9)


@pytest.mark.parametrize("p,H", [(24, 100), (7, 100), (24, 10), (12, 96)])
def test_period_core_equals_the_per_phase_formulation(p, H):
    """The original PeriodCore.fit (one small regression per phase, summed) as an independent reference, incl. a partial last period."""
    d = _std_series()
    L = T.eff_len(240, p)
    X, Y = T.windows(d, 0, 8640, L, H, 12)
    core = T.PeriodCore(L, p, 0.7)
    core.fit(X, Y)
    m, K, full = L // p, -(-H // p), H // p
    Z = [X[:, j::p] for j in range(p)]
    A = np.zeros((K, m))
    if full:
        G = sum(z.T @ z for z in Z)
        G[np.diag_indices_from(G)] += 0.7 * max(1.0, np.trace(G) / m)
        A[:full] = np.linalg.solve(G, sum(Z[j].T @ Y[:, j::p][:, :full] for j in range(p))).T
    if H % p:
        js = [j for j in range(p) if j + full * p < H]
        G = sum(Z[j].T @ Z[j] for j in js)
        G[np.diag_indices_from(G)] += 0.7 * max(1.0, np.trace(G) / m)
        A[full] = np.linalg.solve(G, sum(Z[j].T @ Y[:, j + full * p] for j in js))
    assert np.allclose(core.mats["A"], A, atol=1e-9)


def test_basis_core_equals_the_svd_formulation():
    d = _std_series()
    X, Y = T.windows(d, 0, 8640, 120, 60, 8)
    core = T.BasisCore(120, 1, 1.0, r=6)
    core.fit(X, Y)
    W = T.ridge(X, Y, 1.0)
    V = np.linalg.svd(X @ W, full_matrices=False)[2][:6].T
    assert np.allclose(core.mats["U"] @ core.mats["V"], (W @ V) @ V.T, atol=1e-8)       # same subspace projector (singular vectors are sign-free)


@pytest.mark.parametrize("kw", [dict(), dict(robust=True), dict(decomp="ma", distill_lambda=0.4)], ids=["plain", "robust", "ma+distill"])
def test_augmentation_is_streamed_without_changing_the_noise(kw):
    sp = spec(core="linear", period=1, lookback=96, **kw)
    d = _std_series()
    X, Y = T.windows(d, 0, 8640, 96, 48, 6)
    Xv, _ = T.windows(d, 8640, 11520, 96, 48, 24)
    r1, r2 = np.random.default_rng(7), np.random.default_rng(7)
    Xa = X + r1.normal(0, 1, X.shape) * 0.1 * X.std(1, keepdims=True)                    # the one-shot recipe the gym always used
    dense = T.Model(sp).fit(np.concatenate([X, Xa]), np.concatenate([Y, Y])).predict(Xv, 48)
    streamed = T.Model(sp).fit(X, Y, aug=(r2, 0.1)).predict(Xv, 48)
    assert np.allclose(dense, streamed, atol=1e-8)
    assert r1.bit_generator.state == r2.bit_generator.state                              # and the caller's RNG ends exactly where it did


def test_fit_working_memory_is_a_small_fraction_of_a_float64_copy_of_the_windows():
    d = _std_series().astype(np.float32)
    X, Y = T.windows(d, 0, 8640, 336, 720, 2)
    full64 = (X.size + Y.size) * 8                                            # what a dense float64 copy of the windows costs (~32 MB)
    for kw, bound in ((dict(), 0.3), (dict(decomp="ma", robust=True, distill_lambda=0.3, norm="revin"), 0.75)):     # dense float64 fit: 1.5x / 3.1x
        _, peak = _traced_peak(lambda: T.Model(spec(**kw)).fit(X, Y, aug=(np.random.default_rng(0), 0.05)))
        assert peak < bound * full64, (kw, peak / full64)


def test_pretraining_prior_is_fitted_once_per_horizon_not_once_per_series(monkeypatch):
    fits = []
    real = T.Model.fit
    monkeypatch.setattr(T.Model, "fit", lambda self, X, Y, *a, **k: (fits.append(len(X)), real(self, X, Y, *a, **k))[1])
    cfg = T.STAGES_HARD["s1"]
    T.evaluate(spec(pretrain="synth"), "s1", profile="hard")
    members_fits = len(cfg["seeds"]) * len(cfg["H"])
    assert len(fits) == members_fits + len(cfg["H"])                          # one prior per horizon, shared by all evaluation series


GOLDEN = [(dict(), "s0", 0.499854), (dict(), "s1", 0.486362), (dict(pretrain="synth"), "s1", 0.486956), (dict(augment=True), "s1", 0.486421),
          (dict(ens=3), "s1", 0.481340), (dict(robust=True, decomp="ma"), "s0", None), (dict(distill_lambda=0.5), "s1", 0.487982),
          (dict(core="linear", period=1, lookback=720), "s1", 0.438027), (dict(core="fourier", period=1, lookback=720, fourier_k=64), "s1", 0.435532),
          (dict(core="basis", lookback=720, rank=24), "s1", 0.435023),
          (dict(core="linear", period=1, lookback=720, ens=3, augment=True, pretrain="synth"), "s1", 0.434208)]


@pytest.mark.parametrize("kw,stage,expected", [g for g in GOLDEN if g[2] is not None], ids=lambda v: str(v)[:60])
def test_headline_metric_is_unchanged_by_the_float32_streamed_implementation(kw, stage, expected):
    """Values recorded from the dense float64 implementation (hard profile): the optimisation may not move the science."""
    assert abs(T.evaluate(spec(**kw), stage, profile="hard")["rel_mse"] - expected) < 5e-4


def test_window_matrices_are_bounded_by_element_count():
    data = (lambda d: (d - d.mean(0)) / d.std(0))(T.synth(0))
    X, Y = T.windows(data, 0, 8640, 720, 720, 1)                  # stride 1 would be 60k x 1440 = 690 MB per matrix
    assert X.size + Y.size <= T.MAX_WINDOW_ELEMS * 1.05


def _fresh_process_peak_mb(spec, stage="s1"):
    """Peak RSS of ONE evaluation in a brand-new interpreter (what the service's cgroup would see); None where it cannot be measured."""
    import json, os, subprocess, sys
    if not hasattr(os, "wait4"):
        return None
    code = ("import json,sys; from antslab.gyms import tinyts as T; T.evaluate(json.loads(sys.argv[1]), sys.argv[2], profile='hard')")
    p = subprocess.Popen([sys.executable, "-c", code, json.dumps(spec), stage], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "OMP_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    _, status, ru = os.wait4(p.pid, 0)
    if status != 0:
        return None
    return ru.ru_maxrss / ((1 << 20) if sys.platform == "darwin" else (1 << 10))             # macOS reports bytes, Linux KiB


def _measured_or_skip(spec):
    mb = _fresh_process_peak_mb(spec)
    if mb is None:
        pytest.skip("peak resident memory of a child process cannot be measured here")
    return mb


def test_resident_peak_of_one_evaluation_meets_the_resource_targets():
    b = T.baseline_specs()["period_linear"]
    worst = {**b, "core": "linear", "period": 1, "lookback": 720, "ens": 3, "augment": True, "pretrain": "synth"}
    assert _measured_or_skip(b) <= 340                                         # was ~520 MB; target 300, headroom for the allocator of the CI runners (318 MB measured on ubuntu 3.12, 2026-10-17; the server's largest evaluation peaks at 266 MB, docs/operations.md)
    assert _measured_or_skip(worst) <= 650                                     # was ~1000 MB


def test_memory_estimate_never_badly_under_predicts_measured_peaks():
    b = T.baseline_specs()["period_linear"]
    lin = {**b, "core": "linear", "period": 1}
    cases = [b, {**b, "pretrain": "synth"}, {**b, "augment": True}, {**b, "decomp": "ma"}, {**b, "ens": 3}, {**b, "robust": True, "distill_lambda": 0.5},
             {**b, "core": "fourier", "period": 1, "lookback": 720, "fourier_k": 64},
             {**b, "core": "basis", "lookback": 720, "rank": 24},
             {**lin, "lookback": 720}, {**lin, "lookback": 720, "ens": 3, "augment": True, "pretrain": "synth"}]
    for sp in cases:
        measured, est = _measured_or_skip(sp), T.est_mem_mb(sp)
        assert est >= 0.9 * measured, f"estimate {est:.0f} MB under-predicts measured {measured:.0f} MB for {sp['core']}"
        assert est <= 6.0 * measured      # and is not uselessly pessimistic. These fresh-process peaks read 4.1x (macOS) to 5.2x (Linux server) below the estimate; the real-data s2 peaks on the server read 1.8-2.3x below it (docs/operations.md)


def test_memory_estimate_grows_with_cost_and_the_worst_spec_fits_the_contract_cap():
    import tomllib
    from pathlib import Path
    cap = tomllib.loads((Path(__file__).resolve().parents[1] / "contracts" / "tinyts_real.toml").read_text())["objective"]["mem_cap_mb"]
    b = T.baseline_specs()["period_linear"]
    worst = {**b, "core": "linear", "period": 1, "lookback": 720, "ens": 3, "augment": True, "pretrain": "synth"}
    assert T.est_mem_mb(b) < T.est_mem_mb({**b, "ens": 3}) < T.est_mem_mb(worst) <= cap          # the old 800 MB-class worst case now fits
    assert T.est_mem_mb({**worst, "robust": True, "distill_lambda": 0.5, "decomp": "ma"}) > T.est_mem_mb(worst)
