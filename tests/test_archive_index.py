"""Frozen-round summaries: no live-ledger reads, no best-seed cherry picking."""
import curses
import json
import os
from pathlib import Path

import pytest

from antslab import archive_index as ai
from antslab.bundle import make_bundle
from antslab.ledger import Ledger
from antslab.series import compute_epoch
from antslab.tui import app, model, render, views


@pytest.fixture
def frozen_round(tmp_path, make_contract):
    make_contract()
    contract = tmp_path / "task.toml"
    epoch = compute_epoch(contract)
    workdirs = []
    for seed, values in ((1, [(200, .8), (9999, .001), (250, .9)]), (2, [(220, 1.0)]), (3, [])):
        wd = tmp_path / f"portfolio_s{seed}"
        led = Ledger(wd / "ledger.sqlite")
        led.register_epoch(epoch, {})
        led.event("daemon_started", {"arm": "portfolio", "epoch": epoch})
        for count, value in values:
            tid = led.enqueue("toy", "portfolio", seed, {"a": value, "b": 1, "c": "u"}, "random", stage="s2", epoch=epoch)
            claimed = led.claim(30, epoch=epoch)
            assert claimed["id"] == tid
            led.finish(tid, claimed["attempt"], "done", {"n_params": count, "val_mse": value}, cpu_s=1)
        if not values:
            tid = led.enqueue("toy", "portfolio", seed, {"a": .01, "b": 1, "c": "u"}, "random", stage="s0", epoch=epoch)
            claimed = led.claim(30, epoch=epoch)
            led.finish(tid, claimed["attempt"], "done", {"n_params": 20, "val_mse": .01}, cpu_s=1)
        led.close()
        workdirs.append(wd)
    make_bundle("round-one", tmp_path / "bundles", workdirs)
    return tmp_path / "bundles/round-one.tar.gz", contract


def test_summary_uses_deepest_stage_hard_caps_and_median_with_missing_count(frozen_round, tmp_path):
    bundle, contract = frozen_round
    before = bundle.stat()
    row = ai.build_index(bundle, contract, tmp_path / "cache", "Pilot question", "pilot", "verified local copy")
    catalog = ai.read_index(tmp_path / "cache/index.json")
    detail = ai.read_round(catalog, catalog["rounds"][0])
    assert row["preview"][0]["median"] == pytest.approx(.9)
    assert (row["preview"][0]["n"], row["preview"][0]["total"]) == (2, 3)
    assert detail["runs"][0]["best"]["300"]["n_params"] == 200
    assert detail["runs"][2]["best"] == {}  # cheap-stage winners are never deep-stage results
    assert bundle.stat().st_mtime_ns == before.st_mtime_ns
    original = (tmp_path / "cache/round-one.json").read_bytes()
    ai.build_index(bundle, contract, tmp_path / "cache", "another title")
    assert (tmp_path / "cache/round-one.json").read_bytes() == original
    assert len(ai.read_index(tmp_path / "cache/index.json")["rounds"]) == 1


def test_live_checkpoint_omits_all_comparative_results(frozen_round, tmp_path):
    bundle, contract = frozen_round
    row = ai.build_index(bundle, contract, tmp_path / "cache", kind="snapshot")
    detail = ai.read_round(ai.read_index(tmp_path / "cache/index.json"), row)
    assert all(run["best"] == {} for run in detail["runs"])
    assert row["preview"][0]["median"] is None
    assert "intentionally omitted" in render.plain(views.afterlife_screen({"catalog": {"rounds": [row]}}, views.Ctx()).lines)


def test_wrong_epoch_refuses_to_write_a_mislabelled_summary(frozen_round, tmp_path):
    bundle, contract = frozen_round
    other = tmp_path / "other.toml"
    other.write_text(contract.read_text().replace('name="toy"', 'name="other"'))
    with pytest.raises(ValueError, match="epoch"):
        ai.build_index(bundle, other, tmp_path / "cache", kind="completed")
    assert not (tmp_path / "cache/index.json").exists()


def test_corrupt_bundle_never_creates_index(frozen_round, tmp_path):
    bundle, contract = frozen_round
    raw = bytearray(bundle.read_bytes())
    raw[len(raw) // 2] ^= 255
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(raw)
    with pytest.raises(ValueError, match="verification"):
        ai.build_index(broken, contract, tmp_path / "cache")
    assert not (tmp_path / "cache/index.json").exists()


def test_cache_rejects_tampered_summary_and_nonregular_index(frozen_round, tmp_path):
    bundle, contract = frozen_round
    row = ai.build_index(bundle, contract, tmp_path / "cache")
    catalog = ai.read_index(tmp_path / "cache/index.json")
    (tmp_path / "cache/round-one.json").write_text('{}')
    with pytest.raises(ValueError, match="checksum"):
        ai.read_round(catalog, row)
    with pytest.raises(ValueError, match="round id"):
        ai.read_round(catalog, {"id": "../elsewhere"})
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert "error" in ai.read_index(fifo)  # must return, not wait for a writer


def test_many_rounds_use_selection_window_and_render_without_a_ledger(tmp_path, make_contract, monkeypatch):
    make_contract()
    monkeypatch.setenv("ANTS_ARCHIVE_INDEX", str(tmp_path / "missing.json"))
    a = app.App(str(tmp_path / "task.toml"))
    a.workdirs = []
    monkeypatch.setattr(a, "current", lambda: pytest.fail("Afterlife must not open a live ledger"))
    data = a.load("afterlife", {}, None)
    assert "No past rounds" in render.plain(views.afterlife_screen(data, views.Ctx()).lines)
    rows = [{"id": f"round-{i}", "title": f"Question {i}", "kind": "snapshot", "created_utc": "2026-10-19", "preview": []} for i in range(50)]
    for width in (60, 100, 140):
        view = views.afterlife_screen({"catalog": {"rounds": rows}}, views.Ctx(width=width, height=30, sel=24))
        text = render.plain(views.fit(view.lines, width))
        assert "Question 24" in text and "Question 0" not in text
        assert view.rows == 50 and max(map(len, text.splitlines())) <= width
    a.on_key(ord("9"), curses)
    assert a.screen == "afterlife" and views.SCREENS[-1] == ("afterlife", "Past")
    a.on_key(curses.KEY_LEFT, curses)
    assert a.screen == "system"


def test_afterlife_detail_only_reads_cached_file(frozen_round, tmp_path, monkeypatch):
    bundle, contract = frozen_round
    row = ai.build_index(bundle, contract, tmp_path / "cache", kind="completed")
    monkeypatch.setenv("ANTS_ARCHIVE_INDEX", str(tmp_path / "cache/index.json"))
    a = app.App(str(contract))
    d = app.Data(); d.value = a.load("afterlife", {}, None)
    a.data, a.screen = d, "afterlife"
    monkeypatch.setattr(a, "toggle_pause", lambda: pytest.fail("archived rounds cannot be paused"))
    a.on_key(ord("p"), curses)
    a.on_key(10, curses)
    assert a.screen == "pager" and row["bundle_sha256"] in a.pager_text.replace("\n", "")
    assert "params" in a.pager_text
    assert "SAFE NEXT STEPS" in a.pager_text
    assert max(map(len, a.pager_text.splitlines())) <= a.ctx.width - 4
    a.afterlife_selection = 7
    a.back()
    assert a.screen == "afterlife" and a.ctx.sel == 7


@pytest.mark.parametrize("row", [None, {"id": "../bad"},
    {"id": "round", "preview": [None]},
    {"id": "round", "verified_at": 1e99},
    {"id": "round", "preview": [{"n": 3, "total": 2}]}])
def test_invalid_cache_has_actionable_error_not_a_crash(tmp_path, row):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"format": 1, "rounds": [row]}))
    catalog = ai.read_index(path)
    assert catalog.get("error") and catalog["rounds"] == []
    text = render.plain(views.afterlife_screen({"catalog": catalog}, views.Ctx()).lines)
    assert "could not be read" in text and "No running experiment" in text


def test_missing_detail_explains_safe_next_action(frozen_round, tmp_path, monkeypatch):
    bundle, contract = frozen_round
    ai.build_index(bundle, contract, tmp_path / "cache")
    monkeypatch.setenv("ANTS_ARCHIVE_INDEX", str(tmp_path / "cache/index.json"))
    a = app.App(str(contract))
    d = app.Data(); d.value = a.load("afterlife", {}, None)
    a.data, a.screen = d, "afterlife"
    (tmp_path / "cache/round-one.json").unlink()
    a.on_key(10, curses)
    assert a.screen == "pager"
    assert "No running experiment was changed" in a.pager_text


def test_navigation_registry_covers_every_screen(tmp_path, make_contract):
    make_contract()
    a = app.App(str(tmp_path / "task.toml"))
    for index, (name, _) in enumerate(views.SCREENS):
        a.on_key(ord(str(index)), curses)
        assert a.screen == name
        a.cycle_screen(1)
        assert a.screen == views.SCREENS[(index + 1) % len(views.SCREENS)][0]
        a.cycle_screen(-1)
        assert a.screen == name


def test_series_discovery_does_not_resurrect_archived_work(tmp_path, monkeypatch):
    root = tmp_path / "series"
    live = root / "e43a/portfolio_s1"
    archived = root / "e43a/archive/retired_s1"
    for wd in (live, archived):
        wd.mkdir(parents=True)
        (wd / "ledger.sqlite").touch()
    monkeypatch.setenv("ANTS_SERIES_DIR", str(root))
    found = model.discover_workdirs()
    assert live in found and archived not in found


def test_history_and_catalog_do_not_require_a_live_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTS_ARCHIVE_INDEX", str(tmp_path / "missing.json"))
    monkeypatch.setattr(model, "find_contract", lambda *args: pytest.fail("history/catalog must not resolve a live contract"))
    monkeypatch.setattr(model, "data_overview", lambda: {"sets": []})
    a = app.App()
    a.workdirs = []
    assert a.load("afterlife", {}, None)["catalog"]["rounds"] == []
    assert a.load("data", {}, None) == {"data": {"sets": []}}
    text = app.once(screen="past", color=False)
    assert text.startswith(" Past\n") and "cached records only" in text
    assert "Afterlife" not in text
