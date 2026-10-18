import numpy as np
import pytest
from pathlib import Path
from antslab.gyms import tinyts as T

CAT = Path(__file__).resolve().parents[1] / "data" / "processed" / "etth1.npz"
pytestmark = pytest.mark.skipif(not CAT.exists(), reason="processed datasets not built (ants data build)")
SPEC = T.baseline_specs()["period_linear"]


def test_rolling_blocks_tile_the_late_part_of_train_plus_val_without_gaps_or_overlap():
    ser = T.catalog().load("etth1")
    b = T.rolling_blocks(ser, 3)
    end = ser.n_train + ser.n_val
    assert b[0][0] == int(0.6 * end) and b[-1][1] == end and all(b[i][1] == b[i + 1][0] for i in range(2))
    assert T.rolling_blocks(ser, 1) == [(ser.n_train, end)]                  # folds=1 is exactly the plain validation split


def test_each_fold_trains_only_on_targets_before_its_evaluation_block(monkeypatch):
    import antslab.data as D
    calls = []
    real = D.sample_windows
    def spy(data, lo, hi, L, H, cap, seed, stride=1, with_t=False):
        calls.append((lo, hi)); return real(data, lo, hi, L, H, cap, seed, stride, with_t)
    monkeypatch.setattr(D, "sample_windows", spy)
    T.evaluate_catalog(SPEC, ["etth1"], "val", [96], 3000, 1500, folds=3)
    # per fold: one training call (0, b_i), then ONE evaluation call (b_i, b_i+1): inputs and targets of an unclipped run are the same windows
    assert len(calls) == 6
    for i in range(3):
        train, ev = calls[2 * i], calls[2 * i + 1]
        assert train[0] == 0 and train[1] == ev[0] and train[1] <= ev[1]


def test_targets_are_never_clipped_even_when_inputs_are(monkeypatch):
    import antslab.data as D
    seen = []
    real = D.sample_windows
    def spy(data, lo, hi, L, H, cap, seed, stride=1, with_t=False):
        seen.append((lo, float(np.abs(data).max()))); return real(data, lo, hi, L, H, cap, seed, stride, with_t)
    monkeypatch.setattr(D, "sample_windows", spy)
    T.evaluate_catalog({**SPEC, "clip": True, "clip_z": 1.0}, ["etth1"], "val", [96], 3000, 1500, folds=1)
    ev = [m for lo, m in seen if lo > 0]
    assert len(ev) == 2 and min(ev) <= 1.0 + 1e-6 < max(ev)                  # clipped inputs, raw targets


def test_test_split_is_never_used_by_validation_modes(monkeypatch):
    import antslab.data as D
    seen = []
    real = D.sample_windows
    monkeypatch.setattr(D, "sample_windows", lambda data, lo, hi, *a, **k: (seen.append(hi), real(data, lo, hi, *a, **k))[1])
    ser = T.catalog().load("etth1")
    T.evaluate_catalog(SPEC, ["etth1"], "val", [96], 2000, 1000, folds=3)
    assert max(seen) <= ser.n_train + ser.n_val                              # no window target reaches the test region


def test_pooled_pretraining_uses_only_other_dev_families(monkeypatch):
    T._POOL_CACHE.clear()
    cat = T.catalog()
    used = []
    real_load = cat.load
    monkeypatch.setattr(cat, "load", lambda n: (used.append(n), real_load(n))[1])
    prior = T._pool_prior({**SPEC, "core": "linear", "period": 1, "lookback": 96}, 96, 96, "ETT", cat, cap=500)
    assert prior is not None
    fam = {cat.meta[n]["family"] for n in used}
    assert "ETT" not in fam and fam <= {"Weather", "FX"} and all(cat.meta[n]["role"] == "dev" for n in used)


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "data" / "raw" / "illness_mirror").exists(),
                    reason="needs the standard tier (ili, airline): scripts/fetch_data.sh all")
def test_short_series_and_oversized_horizons_are_handled_not_crashed():
    m = T.evaluate_catalog(SPEC, ["ili", "airline"], "val", None, 2000, 1000)
    assert np.isfinite(m["rel_mse"]) and m["n_tasks"] >= 1
    assert m["n_skipped"] >= 0


def test_adapter_parameters_are_counted_for_pooled_pretraining_too():
    lin = {**SPEC, "core": "linear", "period": 1, "lookback": 96}
    ad = {**lin, "pretrain": "pool", "adapter": "lowrank", "adapter_rank": 4}
    assert T.count_params(ad) == T.count_params(lin) + 4 * (96 + T.PARAM_H)


def test_catalog_seasonal_naive_denominator_ignores_the_clip_lever():
    ser = T.catalog().load("etth1")
    d = ser.standardised()
    m = T.evaluate_catalog({**SPEC, "clip": True, "clip_z": 0.5}, ["etth1"], "val", [96], 3000, 1500, folds=1)
    from antslab.data import sample_windows
    lo, hi = T.rolling_blocks(ser, 1)[0]
    L = min(T.eff_len(336, 24), max(24, (ser.n_train - 96) // 3))
    Lmax = max(L, ser.ref_period)
    Xv, Yv = sample_windows(d, lo, hi, Lmax, 96, min(1500, T.MAX_WINDOW_ELEMS // (Lmax + 96)), 21)
    rp = ser.ref_period
    ref = np.mean((Xv[:, -rp:][:, np.arange(96) % rp] - Yv) ** 2)
    assert abs(m["val_mse"] / m["rel_mse"] - ref) < 1e-4 * ref


def test_pooled_prior_cache_is_bounded_by_bytes_not_entry_count():
    from types import SimpleNamespace
    T._POOL_CACHE.clear()
    big = lambda: SimpleNamespace(core=SimpleNamespace(mats={"W": np.zeros(1_000_000)}))            # 8 MB each
    for i in range(20):
        T._POOL_CACHE[("fake", i)] = big()
    T._pool_prior({**SPEC, "core": "linear", "period": 1, "lookback": 48}, 48, 24, "ETT", T.catalog(), cap=64)
    assert sum(m.core.mats["W"].nbytes for m in T._POOL_CACHE.values() if m) <= 80 << 20
    T._POOL_CACHE.clear()


def test_catalog_evaluation_resident_peak_is_bounded():
    """Fresh interpreter, the s1 protocol (3 datasets x 4 horizons x 3 folds): was ~620 MB, the windows now being float32 and streamed."""
    import json, os, subprocess, sys
    if not hasattr(os, "wait4"):
        pytest.skip("peak resident memory of a child process cannot be measured here")
    code = ("import json,sys; from antslab.gyms import tinyts as T; "
            "T.evaluate_catalog(json.loads(sys.argv[1]), ['etth1', 'weather', 'exchange'], folds=3, cap_tr=15000, cap_va=8000)")
    p = subprocess.Popen([sys.executable, "-c", code, json.dumps(SPEC)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=Path(__file__).resolve().parents[1], env={**os.environ, "OMP_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    _, status, ru = os.wait4(p.pid, 0)
    assert status == 0
    assert ru.ru_maxrss / ((1 << 20) if sys.platform == "darwin" else (1 << 10)) <= 400
