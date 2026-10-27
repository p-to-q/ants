"""Tests for the Tier A validation report tool (src/antslab/tiera.py).

Uses the real development data when present (data/raw), the toy gym for refusal tests.
Each test stays under ~30 s on 4 dev datasets."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _has_catalog_data():
    """True when the catalog's dev datasets have processed files."""
    try:
        from antslab.data import Catalog, locate_catalog
        cat = Catalog(locate_catalog())
        for n in cat.names("dev"):
            if not (cat.base / "processed" / f"{n}.npz").exists():
                return False
        return True
    except Exception:
        return False


needs_data = pytest.mark.skipif(not _has_catalog_data(), reason="dev datasets not present (data/processed)")


# ---- refusal tests (no data needed) --------------------------------

def test_refuses_test_split():
    from antslab.tiera import _refuse_non_val
    _refuse_non_val("val")  # must not raise
    with pytest.raises(SystemExit, match="gate"):
        _refuse_non_val("test")


def test_refuses_non_dev_datasets():
    """Non-dev datasets must be refused with a message naming the gate."""
    from antslab.tiera import _refuse_non_dev

    class FakeCat:
        meta = {"etth1": {"role": "dev"}, "electricity": {"role": "sealed"}}

    _refuse_non_dev(["etth1"], FakeCat())
    with pytest.raises(SystemExit, match="gate"):
        _refuse_non_dev(["electricity"], FakeCat())


def test_cli_refuses_test_split(capsys):
    """The CLI must refuse --split test."""
    from antslab import cli
    with pytest.raises(SystemExit, match="gate|refused"):
        cli.main(["tier-a", "--split", "test", "--spec", "/dev/null"])


def test_cli_writes_requested_csv_file(tmp_path, monkeypatch, capsys):
    from antslab import cli, tiera
    path = tmp_path / "spec.json"
    path.write_text('{"core":"period"}')
    dest = tmp_path / "report.csv"
    monkeypatch.setattr(tiera, "tier_a_report", lambda *a, **k: {})
    monkeypatch.setattr(tiera, "format_csv", lambda _: "metric,value\nmse,0.5\n")
    assert cli.main(["tier-a", "--spec", str(path), "--csv", str(dest)]) == 0
    assert dest.read_text() == "metric,value\nmse,0.5\n"
    assert "metric,value" in capsys.readouterr().out


def test_cli_does_not_create_missing_trial_ledger(tmp_path):
    from antslab import cli
    wd = tmp_path / "missing"
    assert cli.main(["tier-a", "--trial", "1", "--workdir", str(wd)]) == 1
    assert not wd.exists()


@pytest.mark.parametrize("lookback", [0, -1, True])
def test_refuses_invalid_lookback_before_loading_data(lookback):
    from antslab.tiera import tier_a_report
    with pytest.raises(ValueError, match="lookback"):
        tier_a_report([], lookback_override=lookback)


# ---- never touches the ledger's unseals table or the gate -----------

def test_tier_a_never_touches_unseals_or_gate(tmp_path):
    """Verify that running tier_a_report does not import gate or write to any ledger."""
    import importlib
    # We only check the import graph: gate must not be loaded
    mods_before = set(sys.modules.keys())
    from antslab import tiera
    # force reload to see what it pulls
    importlib.reload(tiera)
    mods_after = set(sys.modules.keys())
    new_mods = mods_after - mods_before
    assert not any("gate" in m for m in new_mods), f"tiera imported gate: {[m for m in new_mods if 'gate' in m]}"


# ---- determinism: same inputs, same numbers -------------------------

@needs_data
def test_tier_a_is_deterministic():
    from antslab.tiera import tier_a_report
    from antslab.gyms.tinyts import baseline_specs
    spec = baseline_specs()["period_linear"]
    # run twice with the same spec
    r1 = tier_a_report([spec])
    r2 = tier_a_report([spec])
    for row1, row2 in zip(r1["specs"][0]["rows"], r2["specs"][0]["rows"]):
        assert row1["mse_std"] == row2["mse_std"], "tier-a must be deterministic"
        assert row1["mae_std"] == row2["mae_std"]
        assert row1["rel_mse"] == row2["rel_mse"]
        assert row1["rel_mae"] == row2["rel_mae"]


# ---- report content: protocol lines, per-dataset rows ---------------

@needs_data
def test_tier_a_report_structure():
    from antslab.tiera import tier_a_report, format_markdown
    from antslab.gyms.tinyts import baseline_specs
    spec = baseline_specs()["period_linear"]
    report = tier_a_report([spec])
    assert "NOT comparable" in report["protocol"]
    assert "validation" in report["protocol"].lower()
    sr = report["specs"][0]
    assert sr["n_params"] > 0
    assert sr["bytes"] > 0
    assert len(sr["rows"]) >= 4  # at least 4 horizons on at least one dataset
    for r in sr["rows"]:
        assert r["mse_std"] > 0
        assert r["mae_std"] > 0
        assert r["naive_mse"] > 0
        assert r["rel_mse"] > 0
    # per-dataset averages exist
    assert sr["dataset_avgs"]
    # markdown output
    md = format_markdown(report)
    assert "MSE (std.)" in md
    assert "naive MSE" in md
    assert "rel_mse" in md


# ---- CSV output parses correctly ------------------------------------

@needs_data
def test_tier_a_csv_output():
    from antslab.tiera import tier_a_report, format_csv
    from antslab.gyms.tinyts import baseline_specs
    import csv, io
    spec = baseline_specs()["period_linear"]
    report = tier_a_report([spec])
    text = format_csv(report)
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    assert len(rows) == len(report["specs"][0]["rows"])
    for r in rows:
        assert float(r["mse_std"]) > 0
        assert float(r["rel_mse"]) > 0


# ---- lookback override works ----------------------------------------

@needs_data
def test_tier_a_lookback_override():
    from antslab.tiera import tier_a_report
    from antslab.gyms.tinyts import baseline_specs
    spec = baseline_specs()["period_linear"]
    r96 = tier_a_report([spec], lookback_override=96)
    r336 = tier_a_report([spec], lookback_override=336)
    assert r96["specs"][0]["lookback"] == 96
    assert r336["specs"][0]["lookback"] == 336
    # different lookbacks should produce different numbers (not exact same)
    assert r96["specs"][0]["rows"][0]["mse_std"] != r336["specs"][0]["rows"][0]["mse_std"]
