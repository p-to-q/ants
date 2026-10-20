"""Workspace export and trial diff (workspace.py): documented file set, not_found handling."""
import json
import pytest


def _finish(ledger, gym, arm, i, spec, value, stage="s0"):
    ledger.enqueue(gym, arm, i, spec, "evo", stage=stage, spec_key=f"t{i}")
    t = ledger.claim(60, arm=arm)
    ledger.finish(t["id"], 1, "done", {"val_mse": value, "n_params": 50} if stage == "s0" else {"val_mse": value})
    return t["id"]


def test_export_workspace_produces_the_documented_file_set(ledger, make_contract, tmp_path):
    from antslab.workspace import export_workspace
    c = make_contract()
    for i in range(4):
        _finish(ledger, "toy", "h", i, {"a": 0.1 * i, "b": 5, "c": "u"}, 2.0 - 0.1 * i)
    out = tmp_path / "ws"
    rep = export_workspace(ledger, c, "h", out)
    assert rep["trials"] == 4 and rep["detailed"] >= 1
    assert (out / "trials.jsonl").exists()
    assert (out / "frontier.json").exists()
    assert (out / "failures.jsonl").exists()
    assert (out / "SKILL.md").exists()
    # per-trial detail directories carry spec, metrics and lineage
    detail = list((out / "trials").iterdir())
    assert detail, "at least one trial detail directory expected"
    for d in detail:
        assert (d / "spec.json").exists() and (d / "metrics.json").exists() and (d / "lineage.json").exists()
    # trials.jsonl is newline-delimited JSON, one record per line
    lines = [json.loads(x) for x in (out / "trials.jsonl").read_text().splitlines() if x.strip()]
    assert len(lines) == 4


def test_diff_trials_returns_not_found_for_unknown_ids(ledger, make_contract):
    from antslab.workspace import diff_trials
    c = make_contract()
    tid = _finish(ledger, "toy", "h", 0, {"a": 0.5, "b": 5, "c": "u"}, 2.0)
    d = diff_trials(ledger, c, "no-such-trial", str(tid))
    assert "not_found" in d
    d2 = diff_trials(ledger, c, str(tid), "no-such-trial")
    assert "not_found" in d2


def test_diff_trials_reports_lever_and_stage_differences(ledger, make_contract):
    from antslab.workspace import diff_trials
    c = make_contract()
    a = _finish(ledger, "toy", "h", 0, {"a": 0.5, "b": 5, "c": "u"}, 2.0)
    b = _finish(ledger, "toy", "h", 1, {"a": 0.1, "b": 8, "c": "u"}, 1.0)
    d = diff_trials(ledger, c, str(a), str(b))
    assert "not_found" not in d
    assert d["levers"]["a"] == [0.5, 0.1] and d["levers"]["b"] == [5, 8]
    assert "c" not in d["levers"]                                   # unchanged levers are not reported
    assert d["stages"]["s0"] == [2.0, 1.0]