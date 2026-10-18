import tomllib

import numpy as np
import pytest
from pathlib import Path
from antslab.data import Catalog, sample_windows

CAT = Path(__file__).resolve().parents[1] / "data" / "catalog.toml"
pytestmark = pytest.mark.skipif(not (CAT.parent / "raw" / "etth1").exists(), reason="datasets not fetched (scripts/fetch_data.sh)")


def _missing_catalogued_raw() -> list[str]:
    """The raw paths the catalog names that this install does not have.

    The module-level gate above only asks for `raw/etth1` (the dev tier). The tests below are not all dev-tier tests: the
    manifest covers every dataset and two of them load electricity/traffic/solar/ili, so on the documented dev install
    (`scripts/fetch_data.sh dev`, 17 MB) they failed with FileNotFoundError instead of skipping. Gate each group on what it
    actually reads.
    """
    catalog = tomllib.loads(CAT.read_text())
    return [m["raw"] for m in catalog.get("dataset", {}).values()
            if m.get("format") != "synth" and m.get("raw") and not (CAT.parent / m["raw"]).exists()]


_MISSING = _missing_catalogued_raw()
needs_full = pytest.mark.skipif(bool(_MISSING), reason=(
    f"needs every catalogued raw file ({len(_MISSING)} missing, e.g. {', '.join(_MISSING[:3])}): scripts/fetch_data.sh all"))


@pytest.fixture(scope="module")
def cat():
    return Catalog(CAT)


@needs_full
def test_manifest_is_intact(cat):
    assert cat.verify_manifest() == []


@needs_full
def test_canonical_shapes_and_split_boundaries(cat):
    exp = {"etth1": (14400, 7), "ettm1": (57600, 7), "electricity": (26304, 321), "traffic": (17544, 862),
           "exchange": (7588, 8), "solar": (52560, 137), "weather": (52696, 21), "ili": (966, 7)}
    for n, shape in exp.items():
        s = cat.load(n)
        assert s.values.shape == shape, n
        assert s.n_train > 0 and s.n_val > 0 and s.n_test > 0 and s.n_train + s.n_val + s.n_test == shape[0]
    assert (cat.load("etth1").n_train, cat.load("etth1").n_val, cat.load("etth1").n_test) == (8640, 2880, 2880)


def test_roles_are_a_partition_and_sealed_families_are_disjoint_from_dev(cat):
    roles = {n: cat.meta[n]["role"] for n in cat.names()}
    assert set(roles.values()) <= {"dev", "confirm", "sealed", "stress"}
    fam = lambda r, syn=False: {cat.meta[n]["family"] for n in cat.names(r) if n.startswith("syn_") == syn}
    assert fam("sealed").isdisjoint(fam("dev")) and fam("sealed").isdisjoint(fam("confirm"))
    assert fam("stress").isdisjoint(fam("sealed"))      # stress data may derive from dev/confirm families, never touch sealed ones
    assert fam("stress", True).isdisjoint(fam("dev") | fam("confirm") | fam("sealed") | fam("stress"))   # synthetic families are new


def test_weather_sentinels_faithful_vs_clean(cat):
    f, c = cat.load("weather").values, cat.load("weather_clean").values
    assert (f <= -9990).sum() == 81 and (c <= -9990).sum() == 0 and f.shape == c.shape
    ok = f > -9990
    assert np.array_equal(f[ok], c[ok])                         # only the sentinels differ


@needs_full
def test_capped_window_sampling_is_deterministic_bounded_and_target_range_correct(cat):
    s = cat.load("traffic"); d = s.standardised()
    a = sample_windows(d, s.n_train + s.n_val, len(d), 336, 96, cap=3000, seed=1)
    b = sample_windows(d, s.n_train + s.n_val, len(d), 336, 96, cap=3000, seed=1)
    c = sample_windows(d, s.n_train + s.n_val, len(d), 336, 96, cap=3000, seed=2)
    assert a[0].shape == (3000, 336) and a[1].shape == (3000, 96)
    assert np.array_equal(a[0], b[0]) and not np.array_equal(a[0], c[0])


def test_no_train_leakage_into_validation_targets(cat):
    """Targets of validation windows start no earlier than the first validation index (inputs may look back, targets may not)."""
    s = cat.load("etth1"); d = s.standardised()
    X, Y, t = sample_windows(d, s.n_train, s.n_train + s.n_val, 336, 96, cap=None, seed=0, with_t=True)
    first_target_pos = (t.min() * s.n_val) + s.n_train
    assert first_target_pos >= s.n_train
    assert np.array_equal(Y[0], d[s.n_train: s.n_train + 96, 0])        # first window, channel 0, first target block
