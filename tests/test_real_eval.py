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
    tr, ev = calls[0::2], calls[1::4] if False else None
    # per fold: training call (0, b_i) then two evaluation calls (b_i, b_i+1)
    for i in range(3):
        train, e1, e2 = calls[3 * i], calls[3 * i + 1], calls[3 * i + 2]
        assert train[0] == 0 and train[1] == e1[0] == e2[0] and e1 == e2 and train[1] <= e1[1]


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


def test_short_series_and_oversized_horizons_are_handled_not_crashed():
    m = T.evaluate_catalog(SPEC, ["ili", "airline"], "val", None, 2000, 1000)
    assert np.isfinite(m["rel_mse"]) and m["n_tasks"] >= 1
    assert m["n_skipped"] >= 0


def test_adapter_parameters_are_counted_for_pooled_pretraining_too():
    lin = {**SPEC, "core": "linear", "period": 1, "lookback": 96}
    ad = {**lin, "pretrain": "pool", "adapter": "lowrank", "adapter_rank": 4}
    assert T.count_params(ad) == T.count_params(lin) + 4 * (96 + T.PARAM_H)
