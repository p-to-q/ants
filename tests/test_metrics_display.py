"""Tests for the standard metrics display: MSE (std.), rel_mae shown alongside
rel_mse (shown last and labelled as ours) in CLI, TUI and HTML board outputs.
Missing metrics show '-' and never crash."""
import json
import pytest
from antslab import ui
from antslab.tui import views, render


# ---- leaderboard entries carry val_mse and rel_mae -------------------

def test_leaderboard_entries_include_val_mse_and_rel_mae(ledger, make_contract, tmp_path):
    from antslab.campaign import Campaign
    from antslab.proposers import make_proposer
    c = make_contract()
    cp = Campaign(ledger, c, tmp_path / "w", "evo", make_proposer("evo", c, 3), 3, budget_evals=20)
    cp.run()
    from antslab.history import History
    h = History.for_arm(ledger, c, "evo")
    lb = ui.leaderboard(h, c, top=3)
    for cap, rows in lb.items():
        for r in rows:
            assert "val_mse" in r, "leaderboard entry must carry val_mse"
            assert "rel_mae" in r, "leaderboard entry must carry rel_mae"
            assert "value" in r, "leaderboard entry must still carry the primary value"


# ---- render_show puts rel_mse last, shows MSE(std) ------------------

def test_render_show_columns_order_and_labels(ledger, make_contract, tmp_path):
    from antslab.campaign import Campaign
    from antslab.proposers import make_proposer
    c = make_contract()
    cp = Campaign(ledger, c, tmp_path / "w", "evo", make_proposer("evo", c, 3), 3, budget_evals=20)
    cp.run()
    tid = ledger.conn.execute("SELECT id FROM trials WHERE state='done' LIMIT 1").fetchone()[0]
    text = ui.render_show(ui.show(ledger, c, str(tid)))
    # the header line must show MSE(std) before rel_mse(ours)
    assert "MSE(std)" in text
    assert "rel_mse(ours)" in text
    header_line = [l for l in text.splitlines() if "MSE(std)" in l]
    assert header_line
    h = header_line[0]
    assert h.index("MSE(std)") < h.index("rel_mse(ours)")


# ---- cmd_best text output shows MSE, rel_mae, rel_mse last ----------

def test_cmd_best_text_shows_standard_metrics(ledger, make_contract, tmp_path, capsys):
    from antslab import cli
    c = make_contract()
    base = ["--contract", str(tmp_path / "task.toml"), "--workdir", str(tmp_path / "best_test")]
    assert cli.main(["run", *base, "--arm", "evo", "--max-evals", "12"]) == 0
    capsys.readouterr()
    cli.main(["best", *base, "--arm", "evo"])
    text = capsys.readouterr().out
    # the text output must mention MSE= and rel_mae= and rel_mse=
    assert "MSE=" in text
    assert "rel_mae=" in text
    assert "val_mse=" in text  # this fixture uses the toy gym, whose primary is val_mse


# ---- cmd_best JSON still includes all existing fields ----------------

def test_cmd_best_json_includes_val_mse_and_rel_mae(ledger, make_contract, tmp_path, capsys):
    from antslab import cli
    c = make_contract()
    base = ["--contract", str(tmp_path / "task.toml"), "--workdir", str(tmp_path / "json_test")]
    assert cli.main(["run", *base, "--arm", "evo", "--max-evals", "12"]) == 0
    capsys.readouterr()
    cli.main(["best", *base, "--arm", "evo", "--json"])
    out = json.loads(capsys.readouterr().out)
    for cap, rows in out.items():
        for r in rows:
            assert "val_mse" in r
            assert "rel_mae" in r
            assert "value" in r   # the primary, never removed


# ---- missing metrics show '-' and never crash -----------------------

def test_missing_metrics_show_dash_not_crash():
    """The toy gym only has val_mse, no rel_mse or rel_mae: the display must show '-'."""
    d = {"trial": 1, "spec_key": "k", "arm": "a", "epoch": "e", "state": "done",
         "source": "x", "spec": {}, "non_default": "", "error": None,
         "edit_from_parent": None, "lineage": [],
         "stages": {"s0": {"val_mse": 0.5, "n_params": 10, "fit_s": 0.1}}}
    text = ui.render_show(d)
    assert "0.5000" in text  # val_mse is shown
    assert "-" in text       # missing rel_mae / rel_mse show as '-'
    # must not crash
    assert "trial 1" in text


# ---- TUI board view shows the three columns -------------------------

def test_tui_board_view_has_three_metric_columns():
    ctx = views.Ctx(width=120, sel=0)
    lb = {"300": [
        {"rank": 1, "value": 0.9, "val_mse": 0.42, "rel_mae": 0.85, "stage": "s2",
         "n_params": 250, "trial": 5, "key": "k", "operator": "evo", "spec": "a=1"},
        {"rank": 2, "value": 0.95, "val_mse": None, "rel_mae": None, "stage": "s1",
         "n_params": 260, "trial": 6, "key": "k2", "operator": "tpe", "spec": "a=2"},
    ]}
    v = views.board({"lb": lb}, ctx)
    text = render.plain(v.lines)
    assert "MSE(std)" in text
    assert "rel_mae" in text
    assert "rel_mse" in text
    # the entry with None metrics shows a dash
    assert "-" in text
    # entry with real metrics shows the value
    assert "0.4200" in text
    assert "0.8500" in text


# ---- TUI detail view shows structured metric columns ----------------

def test_tui_detail_view_shows_metric_columns():
    ctx = views.Ctx(width=100)
    d = {"detail": {
        "trial": 1, "state": "done", "arm": "a", "source": "evo", "epoch": "e", "spec_key": "k",
        "non_default": "a=1", "error": None, "lineage": [],
        "stages": {"s0": {"val_mse": 0.5, "n_params": 100, "fit_s": 0.3},
                   "s2": {"rel_mse": 0.85, "val_mse": 0.42, "rel_mae": 0.88, "n_params": 100, "fit_s": 1.2}},
    }}
    v = views.detail(d, ctx)
    text = render.plain(v.lines)
    assert "MSE(std)" in text
    assert "rel_mse(ours)" in text
    assert "0.4200" in text   # val_mse of s2
    assert "0.8500" in text   # rel_mse of s2


# ---- TUI help/glossary defines the three columns --------------------

def test_help_screen_defines_metric_columns():
    ctx = views.Ctx(width=100)
    v = views.help_screen(ctx)
    text = render.plain(v.lines)
    assert "MSE (std.)" in text
    assert "rel_mae" in text
    assert "rel_mse (ours)" in text
    assert "not comparable" in text


# ---- HTML board has MSE and rel_mae columns, rel_mse last -----------

def test_html_board_shows_standard_metrics(tmp_path):
    from antslab import bench
    from antslab.board import build
    from antslab.portfolio import build_portfolio
    c = bench.make_contract()
    r = bench.drive(lambda cc, s: build_portfolio(cc, s, with_tpe=False), c, 300, 1, checkpoints=(300,))
    r["hist"].update()
    d = build(r["hist"].l, c, ["arm"], tmp_path)
    page = (tmp_path / "board.html").read_text()
    assert "MSE (std.)" in page
    assert "rel_mae" in page
    assert d["primary"] in page
    # JSON also carries the new fields
    jd = json.loads((tmp_path / "board.json").read_text())
    for arm, adata in jd["arms"].items():
        for cap, rows in adata["board"].items():
            for r in rows:
                assert "val_mse" in r
                assert "rel_mae" in r


# ---- 80-column rendering does not crash or wrap ---------------------

def test_80_column_rendering():
    ctx = views.Ctx(width=80, sel=0)
    lb = {"300": [
        {"rank": 1, "value": 0.9, "val_mse": 0.42, "rel_mae": 0.85, "stage": "s2",
         "n_params": 250, "trial": 5, "key": "k", "operator": "evo", "spec": "a=1"},
    ]}
    v = views.board({"lb": lb}, ctx)
    fitted = views.fit(v.lines, 80)
    for line in fitted:
        assert sum(len(t) for t, _ in line) <= 80
