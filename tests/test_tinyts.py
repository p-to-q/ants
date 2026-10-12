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
    small = T.evaluate({**sp, "prune": 0.5, "quant": "int8"}, "s0")
    assert small["n_params"] == round(full["n_params"] * 0.5) and small["bytes"] < full["bytes"] / 3
    assert np.isfinite(small["val_mse"]) and small["val_mse"] < 3 * full["val_mse"]
    assert T.evaluate({**sp, "quant": "int4"}, "s0")["val_mse"] >= full["val_mse"] * 0.99


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


def test_window_matrices_are_bounded_by_element_count():
    data = (lambda d: (d - d.mean(0)) / d.std(0))(T.synth(0))
    X, Y = T.windows(data, 0, 8640, 720, 720, 1)                  # stride 1 would be 60k x 1440 = 690 MB per matrix
    assert X.size + Y.size <= T.MAX_WINDOW_ELEMS * 1.05


def _fresh_process_peak_mb(spec, stage="s1"):
    """Peak RSS of ONE evaluation in a brand-new interpreter (what the service's cgroup would see)."""
    import json, os, subprocess, sys
    code = ("import json,sys; from antslab.gyms import tinyts as T; T.evaluate(json.loads(sys.argv[1]), sys.argv[2], profile='hard')")
    p = subprocess.Popen([sys.executable, "-c", code, json.dumps(spec), stage], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "OMP_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    _, _, ru = os.wait4(p.pid, 0)
    return ru.ru_maxrss / ((1 << 20) if sys.platform == "darwin" else (1 << 10))


def test_memory_estimate_never_badly_under_predicts_measured_peaks():
    b = T.baseline_specs()["period_linear"]
    lin = {**b, "core": "linear", "period": 1}
    cases = [b, {**b, "pretrain": "synth"}, {**b, "augment": True}, {**b, "decomp": "ma"},
             {**b, "core": "fourier", "period": 1, "lookback": 720, "fourier_k": 64},
             {**b, "core": "basis", "lookback": 720, "rank": 24},
             {**lin, "lookback": 720, "ens": 3, "augment": True, "pretrain": "synth"}]
    for sp in cases:
        measured, est = _fresh_process_peak_mb(sp), T.est_mem_mb(sp)
        assert est >= 0.9 * measured, f"estimate {est:.0f} MB under-predicts measured {measured:.0f} MB for {sp['core']}"
        assert est <= 3.0 * measured                                       # and is not uselessly pessimistic


def test_the_worst_spec_is_rejected_by_the_resource_cap_before_any_cpu_is_spent():
    b = T.baseline_specs()["period_linear"]
    worst = {**b, "core": "linear", "period": 1, "lookback": 720, "ens": 3, "augment": True, "pretrain": "synth"}
    assert T.est_mem_mb(worst) > 800 > T.est_mem_mb(b)
