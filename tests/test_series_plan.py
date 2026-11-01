from antslab.series import plan, load_series, collect_status
from pathlib import Path
import pytest


def _series():
    return load_series(Path(__file__).resolve().parents[1] / "deploy/series/e43a.toml")


def test_plan_numbers():
    p = plan(_series(), workers=2, cpu_count=2, mem_available_mb=4000)
    assert p["runs"] == 16 and p["total_cpu_hours"] == 224.0 and p["notes"] == []
    assert p["expected_wall_hours"] > 100


def test_plan_warns_on_oversubscription_and_memory():
    p = plan(_series(), workers=4, cpu_count=2, mem_available_mb=500)
    assert len(p["notes"]) == 2


def test_status_hides_values_by_default(tmp_path):
    s = _series()
    rows, _ = collect_status(s, str(Path("contracts/default.toml")) if Path("contracts/default.toml").exists() else "", tmp_path)
    assert all("best_per_cap" not in r for r in rows)
    assert all(r["state"] == "not started" for r in rows)


def test_nonempty_status_never_reads_scores_unless_requested(tmp_path, make_contract, monkeypatch):
    from antslab.ledger import Ledger
    from antslab import seeds, series
    c = make_contract()
    s = {**_series(), "arms": ["random"], "seeds_parsed": [1], "max_cpu_s": 10}
    wd = tmp_path / "random_s1"
    led = Ledger(wd / "ledger.sqlite")
    led.enqueue(c.gym, "random", 1, {"a": .2, "b": 2, "c": "u"}, "random")
    t = led.claim(60, c.gym, "random")
    led.finish(t["id"], t["attempt"], "done", metrics={"val_mse": .1, "n_params": 5}, cpu_s=11)
    led.close()
    calls = []
    monkeypatch.setattr(seeds, "_best_per_cap", lambda *a: calls.append(a) or {"100": .1})
    monkeypatch.setattr(series, "compute_epoch", lambda _: s["epoch"])
    rows, _ = collect_status(s, str(tmp_path / "task.toml"), tmp_path)
    assert rows[0]["state"] == "done" and rows[0]["cpu_s"] == 11
    assert "best_per_cap" not in rows[0] and calls == []
    rows, _ = collect_status(s, str(tmp_path / "task.toml"), tmp_path, values=True)
    assert rows[0]["best_per_cap"] == {"100": .1} and len(calls) == 1


def test_corrupt_ledger_is_an_error_not_missing_history(tmp_path):
    s = {**_series(), "arms": ["random"], "seeds_parsed": [1]}
    path = tmp_path / "random_s1/ledger.sqlite"
    path.parent.mkdir()
    path.write_bytes(b"not a SQLite database")
    rows, _ = collect_status(s, "", tmp_path)
    assert rows[0]["state"].startswith("error:")


@pytest.mark.parametrize("workers", [0, -1, 9])
def test_plan_refuses_invalid_worker_counts(workers):
    with pytest.raises(ValueError, match="workers"):
        plan(_series(), workers=workers)


def test_plan_does_not_treat_oversubscribed_workers_as_extra_cpus():
    a = plan(_series(), workers=2, cpu_count=2)
    b = plan(_series(), workers=4, cpu_count=2)
    assert a["expected_wall_hours"] == b["expected_wall_hours"]
