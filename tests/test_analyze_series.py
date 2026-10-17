import importlib.util
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_series.py"
spec = importlib.util.spec_from_file_location("analyze_series", SCRIPT)
az = importlib.util.module_from_spec(spec)
spec.loader.exec_module(az)


def test_the_exact_interval_covers_as_often_as_it_says():
    rng = np.random.default_rng(3)
    for n in (6, 8):
        hit, claimed = 0, 0.0
        for _ in range(2500):
            d = rng.standard_normal(n) * 0.04 + 0.03
            _, (lo, hi), claimed = az.hl_exact(d)
            hit += lo <= 0.03 <= hi
        assert abs(hit / 2500 - claimed) < 0.02 and claimed >= 0.95


def test_exact_tests_and_a12_on_known_cases():
    d = -np.arange(1, 9) * 0.01
    assert az.perm_p(d) == pytest.approx(2 / 256)                  # all eight differences on one side: the smallest two-sided p there is
    assert az._signrank_dist(8).sum() == 256 and az._signrank_dist(8)[0] == 1
    assert az.a12(np.array([.8, .9, .85]), np.array([.7, .75, .95])) == pytest.approx(6 / 9)
    assert az.a12(np.array([.8]), np.array([.8])) == 0.5


def test_runs_without_a_result_are_censored_not_dropped():
    run = {"s2": [(100.0, 0.9, 250), (900.0, 0.85, 250), (950.0, 0.8, 2500)], "cum": 1000.0}
    assert az.value_at(run, 300, 500) == 0.9 and az.value_at(run, 300, 1000) == 0.85
    assert np.isnan(az.value_at(run, 100, 1000)) and az.value_c(run, 100, 1000) == 1.0         # no model that small yet: the reference, not a missing pair
    assert az.time_to(run, 300, 0.86, 1000) == 900.0 and az.time_to(run, 300, 0.5, 1000) == 1000.0       # never reached: censored at the budget


def test_summary_reads_a_clear_effect_and_a_null(tmp_path):
    rng = np.random.default_rng(0)
    a = 0.85 + 0.01 * rng.standard_normal(8)
    s = az.summary(a, a - 0.06, rng)
    assert s["median_diff"] == pytest.approx(-0.06) and s["wilcoxon_p"] == pytest.approx(2 / 256) and az.verdict(s, 0.0294).startswith("evidence")
    s0 = az.summary(a, a + 0.02 * rng.standard_normal(8), rng)
    assert az.verdict(s0, 0.0294).split(":")[0] in ("inconclusive", "futility at this look")
    assert s["mde80"] >= 0 and az.verdict({"n": 2}, 0.05) == "too few pairs"


def test_load_run_reads_a_ledger_read_only_and_cumulates_cpu(ledger):
    for i, (stage, cpu, rm, n) in enumerate([("s0", 1.0, 0.95, 250), ("s1", 30.0, 0.9, 250), ("s2", 70.0, 0.88, 250)]):
        ledger.enqueue("toy", "arm", i, {"a": 0.1, "b": 2, "c": "u"}, "random", stage=stage)
        t = ledger.claim(60, "toy", "arm")
        ledger.finish(t["id"], t["attempt"], "done", metrics={"rel_mse": rm, "n_params": n}, cpu_s=cpu)
    ledger.conn.commit() if hasattr(ledger.conn, "commit") else None
    run = az.load_run(Path(ledger.path).parent)
    assert run["cum"] == pytest.approx(101.0) and run["s2"] == [(101.0, 0.88, 250)] and run["chain_ok"] is True
