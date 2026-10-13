"""The terminal interface: views are pure and testable without a terminal; one pty test starts the real curses loop."""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from antslab import cli
from antslab.ledger import Ledger
from antslab.tui import app, model, pixel, render, views

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    """a real, small campaign on the toy gym: ledger, trials in several stages, events"""
    from .conftest import TOML, TOY
    root = tmp_path_factory.mktemp("tui")
    contract = root / "task.toml"
    contract.write_text(TOML.format(toy=TOY, timeout=20))
    wd = root / "wd"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract), "--workdir", str(wd), "--arm", "evo", "--max-evals", "14"],
                       capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0, r.stderr[-400:]
    return contract, wd


def screens(contract, wd, width=100):
    out = {}
    for s in ("home", "overview", "board", "trials", "fleet", "events", "health", "system"):
        out[s] = app.once(str(contract), str(wd), None, width, s, color=False)
    return out


def test_every_screen_renders_from_a_real_ledger_within_the_width(run_dir):
    contract, wd = run_dir
    for width in (60, 100, 140):
        for name, text in screens(contract, wd, width).items():
            assert text.strip(), name
            assert max(len(l) for l in text.splitlines()) <= width + 2, (name, width)
    h = app.once(str(contract), str(wd), None, 100, "home", color=False)
    assert "Autonomous Navigation of Tiny-model Space" in h and "▀" in h and "evo" in h


def test_reading_the_ledger_changes_nothing_on_disk(run_dir):
    contract, wd = run_dir
    files = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in wd.glob("ledger.sqlite*")}
    src = model.Source(wd, str(contract))
    src.overview(); src.leaderboard(); src.trials(); src.events(); src.health(); src.detail(1); model.fleet([src]); model.system_info(src)
    src.close()
    assert files == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in wd.glob("ledger.sqlite*")}


def test_source_connection_cannot_write(run_dir):
    import sqlite3
    contract, wd = run_dir
    src = model.Source(wd, str(contract))
    with pytest.raises(sqlite3.OperationalError):
        src.led.conn.execute("DELETE FROM trials")
    src.close()


def test_views_are_pure_functions_of_their_data():
    ctx = views.Ctx(width=100, sel=1)
    lb = {"300": [{"rank": 1, "value": 0.9, "stage": "s2", "n_params": 250, "trial": 5, "key": "k", "operator": "evo", "spec": "a=1"},
                  {"rank": 2, "value": 0.95, "stage": "s2", "n_params": 260, "trial": 6, "key": "k2", "operator": "tpe", "spec": "a=2"}]}
    v = views.board({"lb": lb}, ctx)
    assert v.rows == 2 and "evo" in "".join(t for t, _ in v.lines[v.row0]) and "▌" in "".join(t for t, _ in v.lines[v.row0 + 1])
    assert views.board({"lb": {}}, ctx).lines[0][0][0].strip() == "no finished evaluations yet"
    assert views.health({"rows": [("OK", "fine"), ("FAIL", "broken")]}, ctx).lines[0][0][0].strip() == "1 failing, 0 warnings"


def test_text_ansi_and_html_agree_and_escape():
    lines = [[("a<b", "bad"), (" ok", "dim"), ("▀", ("ink", "mint"))]]
    assert render.plain(lines) == "a<b ok▀"
    assert "\x1b[" in render.ansi(lines) and render.ansi(lines).count("\x1b[0m") == 3
    h = render.to_html(lines)
    assert "a&lt;b" in h and "<script" not in h and "linear-gradient" in h


def test_pixel_art_is_what_it_claims():
    art = pixel._ANT_ART
    assert len(art) == 16 and all(len(r) == 30 for r in art) and set("".join(art)) == {"m", "i", "d"}
    ant = pixel.ant_lines()
    assert len(ant) == 8 and all(sum(len(t) for t, _ in l) == 30 for l in ant)
    assert pixel.bar(0, 10, 8) == " " * 8 and pixel.bar(10, 10, 8) == "█" * 8 and pixel.bar(5, 10, 8).strip() == "████"
    assert pixel.bar(99, 10, 4) == "████" and pixel.bar(-1, 10, 4) == "    " and pixel.bar(1, 0, 4) == "    "
    assert pixel.spark([1, 1, 1]) == "▄▄▄" and pixel.spark([0, 8])[0] == "▁" and pixel.spark([0, 8])[-1] == "█" and pixel.spark([]) == ""
    assert all(sum(len(t) for t, _ in l) == sum(len(t) for t, _ in pixel.word_lines("ants")[0]) for l in pixel.word_lines("ants"))


def test_command_line_runs_read_only_commands_and_refuses_the_rest(run_dir):
    contract, wd = run_dir
    a = app.App(str(contract), str(wd))
    a.run_command("best --top 1")
    assert a.screen == "pager" and "cap <=" in a.pager_text
    a.run_command("sql SELECT COUNT(*) AS n FROM trials")
    assert "n" in a.pager_text and a.pager_title.startswith("sql")
    for forbidden in ("run --max-evals 1", "restore --force", "service install", "janitor", "data fetch", "update --apply"):
        a.run_command(forbidden)
        assert "not allowed" in a.pager_text, forbidden
    a.run_command("sql DELETE FROM trials")
    assert "only SELECT" in a.pager_text
    a.run_command("hint try a longer look-back")
    assert "recorded" in a.pager_text
    assert Ledger.open_readonly(wd / "ledger.sqlite").events("user_hint")


def test_a_screen_that_cannot_load_says_why_instead_of_crashing(tmp_path):
    a = app.App(str(ROOT / "contracts/tinyts_synth_hard.toml"), None)
    a.workdirs = []
    d = app.Data(); d.error = "FileNotFoundError: no ledger found"; d.at = 1
    a.data = d; a.screen = "overview"
    assert "cannot show this screen" in render.plain(a.view().lines)


def test_bare_ants_uses_the_interface_only_on_a_terminal(monkeypatch, run_dir):
    contract, wd = run_dir
    monkeypatch.setenv("ANTS_WORKDIR", str(wd))
    monkeypatch.setattr(cli, "_can_tui", lambda: False)
    called = {}
    monkeypatch.setattr(cli, "cmd_shell", lambda a: called.setdefault("shell", True) or 0)
    monkeypatch.setattr(cli, "cmd_ui", lambda a: called.setdefault("ui", True) or 0)
    cli.main([])
    assert called == {"shell": True} or "shell" in called


def _winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a pty")
def test_the_real_curses_loop_starts_navigates_and_quits_cleanly(run_dir):
    contract, wd = run_dir
    master, slave = pty.openpty()
    _winsize(slave, 40, 120)
    env = {**os.environ, "TERM": "xterm-256color", "ANTS_HOME": str(wd.parent / "h"), "ANTS_NO_UPDATE_CHECK": "1"}
    p = subprocess.Popen([sys.executable, "-m", "antslab.cli", "ui", "--contract", str(contract), "--workdir", str(wd)],
                         stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env, close_fds=True)
    os.close(slave)
    out = b""

    def pump(seconds):
        nonlocal out
        end = time.time() + seconds
        while time.time() < end:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    out += os.read(master, 65536)
                except OSError:
                    return
    try:
        pump(2.5)
        for key in (b"2", b"3", b"\r", b"\x1b", b"6", b"?", b"\x1b", b"1", b"/", b"best --top 1\r", b"\x1b", b"q"):
            os.write(master, key)
            pump(0.5)
        rc = p.wait(timeout=15)
    finally:
        if p.poll() is None:
            p.kill()
        os.close(master)
    err = p.stderr.read().decode(errors="replace")
    text = out.decode("utf-8", errors="replace")
    assert rc == 0, (rc, err[-500:])
    assert "Traceback" not in err and "Traceback" not in text
    assert "Autonomous Navigation" in text and "Leaderboard" in text or "evo" in text


def test_a_working_directory_the_service_user_cannot_enter_does_not_break_discovery(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    (locked / "runs" / "x").mkdir(parents=True)
    Ledger(locked / "runs" / "x" / "ledger.sqlite").close()
    locked.chmod(0o000)
    try:
        monkeypatch.chdir(locked) if False else None
        if os.access(locked, os.R_OK):
            pytest.skip("directory permissions are not enforced here (root)")
        from antslab.home import ledgers_in
        assert ledgers_in(locked / "runs") == []
        assert isinstance(model.discover_workdirs(), list)
    finally:
        locked.chmod(0o700)


def test_arms_are_compared_at_equal_budget(run_dir, tmp_path, monkeypatch):
    contract, wd = run_dir
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "h"))
    for k in ("ANTS_WORKDIR", "ANTS_CONTRACT"):
        monkeypatch.delenv(k, raising=False)
    a = model.Source(wd, str(contract))
    n_all = len(model.deep_series(a))
    assert n_all >= 1
    one = model.equal_budget([a])
    assert one["n"] == n_all and one["rows"][0]["n"] == n_all
    first = model.best_at(a, 1)
    assert len(first) == 1                                      # one evaluation lands in exactly one cap bin
    assert all(first[k] >= model.best_at(a, n_all)[k] for k in first)       # more evaluations never make the best worse (primary is minimised)
    text = app.once(str(contract), str(wd), None, 100, "fleet", color=False)
    assert "At equal budget" in text and "one seed per search" in text
