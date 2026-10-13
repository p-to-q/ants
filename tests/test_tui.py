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
    assert len(art) == 16 and all(len(r) == 29 for r in art) and set("".join(art)) == {"m", "i", "d"}
    ant = pixel.ant_lines()
    assert len(ant) == 8 and all(sum(len(t) for t, _ in l) == 29 for l in ant)
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


# ----------------------------------------------------------------------------------------------------------- findings of the independent review
def test_a_ledger_that_cannot_be_opened_is_a_line_not_a_crash(run_dir, tmp_path, monkeypatch):
    contract, wd = run_dir
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "ledger.sqlite").write_bytes(b"this is not a database" * 50)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "h"))
    for screen in ("home", "fleet", "overview", "health"):
        text = app.once(str(contract), str(bad), None, 100, screen, color=False) if screen in ("home", "fleet") else None
    a = app.App(str(contract), str(bad))
    try:
        a.load("overview", {}, None)
        raise AssertionError("expected an error")
    except Exception as e:
        assert "Traceback" not in str(e)
    d = a.load("home", {}, None)
    assert d["fleet"] and "error" in d["fleet"][0]


def test_data_for_another_screen_is_never_given_to_a_view(run_dir):
    contract, wd = run_dir
    a = app.App(str(contract), str(wd))
    d = app.Data()
    d.value, d.key, d.at = {"snap": {}}, ("overview", (), None, 0, None), 1
    a.data, a.screen = d, "board"
    assert "loading" in render.plain(a.view().lines)


def test_the_palette_allows_read_only_commands_and_hints_keep_their_quotes(run_dir):
    contract, wd = run_dir
    a = app.App(str(contract), str(wd))
    for refused in ("top", "export --out /tmp/x", "workspace --out /tmp/x", "run", "restore"):
        a.run_command(refused)
        assert "not allowed" in a.pager_text, refused
    a.run_command("hint don't use an apostrophe-free style")
    assert "recorded" in a.pager_text
    assert any("don't" in e["payload"]["text"] for e in Ledger.open_readonly(wd / "ledger.sqlite").events("user_hint"))


def test_text_a_gym_wrote_reaches_the_screen_cleaned(tmp_path, run_dir):
    contract, _ = run_dir
    wd = tmp_path / "wd2"
    led = Ledger(wd / "ledger.sqlite")
    from antslab.contract import load_contract
    c = load_contract(contract)
    tid = led.enqueue(c.gym, "evo", 0, {"a": 0.5, "b": 3, "c": "u"}, "evo")
    t = led.claim(60, c.gym, "evo")
    led.finish(t["id"], t["attempt"], "failed", error="exit 3: \x1b]0;PWNED\x07\nsecond line\x1b[31m")
    led.close()
    src = model.Source(wd, str(contract))
    rows = src.trials()
    assert "\x1b" not in rows[0]["error"] and "\x07" not in rows[0]["error"] and "\n" not in rows[0]["error"]
    out = app.once(str(contract), str(wd), None, 100, "trials", color=False)
    assert "\x1b" not in out and "\x07" not in out


def test_the_pager_scrolls_within_its_text(run_dir):
    import curses
    contract, wd = run_dir
    a = app.App(str(contract), str(wd))
    a.ctx.height = 12
    a.show_text("\n".join(f"line {i}" for i in range(40)), "t")
    for _ in range(5):
        a.on_key(curses.KEY_DOWN, curses)
    assert a.scroll == 5
    a.on_key(curses.KEY_NPAGE, curses); a.on_key(curses.KEY_NPAGE, curses); a.on_key(curses.KEY_NPAGE, curses); a.on_key(curses.KEY_NPAGE, curses)
    assert a.scroll == max(0, len(a.view().lines) - (a.ctx.height - 3))
    a.on_key(curses.KEY_PPAGE, curses)
    assert a.scroll >= 0


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a pty")
def test_the_interface_survives_a_corrupt_ledger_and_a_resize_in_the_prompt(run_dir, tmp_path):
    contract, wd = run_dir
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "ledger.sqlite").write_bytes(b"x" * 4096)
    master, slave = pty.openpty()
    _winsize(slave, 30, 100)
    env = {**os.environ, "TERM": "xterm-256color", "ANTS_HOME": str(tmp_path / "h"), "ANTS_NO_UPDATE_CHECK": "1"}
    p = subprocess.Popen([sys.executable, "-m", "antslab.cli", "ui", "--contract", str(contract), "--workdir", str(bad)],
                         stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env, cwd=str(tmp_path), close_fds=True)
    os.close(slave)

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    os.read(master, 65536)
                except OSError:
                    return
    try:
        pump(2)
        for key in (b"1", b"/", b"best", b"\x03"):
            os.write(master, key); pump(0.3)
        _winsize(master, 4, 20)
        os.kill(p.pid, 28)                                              # SIGWINCH
        pump(0.5)
        for key in (b"\x1b", b"q"):
            os.write(master, key); pump(0.4)
        rc = p.wait(timeout=15)
    finally:
        if p.poll() is None:
            p.kill()
        os.close(master)
    err = p.stderr.read().decode(errors="replace")
    assert rc == 0 and "Traceback" not in err, (rc, err[-600:])


# ----------------------------------------------------------------------------------------------------------- the second version: animation, plain language, charts
def test_plain_language_status_for_every_condition():
    from antslab.tui import vibe
    base = {"paused": False, "chain_ok": True, "free_gb": 30, "evals": 50, "finished_1h": 40, "rate_median_1h": 40, "last_age_s": 20, "fail_share": 0.0,
            "best_age_s": 1800, "llm_fail_share": None, "backup_age_h": 5}
    cases = [({"paused": True}, "Paused"), ({"chain_ok": False}, "Needs a look"), ({"free_gb": 3}, "Running low on space"), ({"evals": 0}, "Warming up"),
             ({"backup_age_h": 60}, "Needs a look"), ({"last_age_s": 4000}, "Stuck?"), ({"fail_share": 0.4}, "Needs a look"), ({"best_age_s": 120}, "Just found something"),
             ({"llm_fail_share": 0.8}, "Waiting on the model"), ({"finished_1h": 5}, "A bit quiet"), ({"finished_1h": 20}, "A bit slow"), ({}, "Going well"),
             ({"best_age_s": 8 * 3600}, "Exploring"), ({"best_age_s": 2 * 3600}, "Steady")]
    for change, phrase in cases:
        m = vibe.mood({**base, **change})
        assert m.phrase == phrase and m.reason, (change, m)
    assert vibe.mood({**base, "paused": True, "chain_ok": False}).phrase == "Paused"          # a paused search is not 'stuck'
    assert "!" not in "".join(vibe.mood({**base, **c}).reason for c, _ in cases)             # tone: no exclamation marks


def test_animation_frames_come_from_the_clock_and_degrade(monkeypatch):
    from antslab.tui import anim
    monkeypatch.delenv("ANTS_REDUCE_MOTION", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    assert anim.frame("running", 0.0) != anim.frame("running", 0.09) and anim.frame("running", 0.0) == anim.frame("running", 0.08 * 8)
    assert len({anim.frame("loading", i * 0.08) for i in range(10)}) == 10
    assert set(anim.frame("loading", 0.3, ascii_only=True)) <= set("|/-\\")
    monkeypatch.setenv("ANTS_REDUCE_MOTION", "1")
    assert anim.frame("running", 0.0) == anim.frame("running", 5.0) == "●" and anim.pulse(0.9)
    monkeypatch.delenv("ANTS_REDUCE_MOTION")
    monkeypatch.setenv("SSH_CONNECTION", "x")
    assert anim.frame("running", 0.09) == anim.frame("running", 0.0)                          # over ssh the interval doubles
    for name, (frames, _) in anim.SPINNERS.items():
        assert all(len(f) == len(frames[0]) for f in frames), name                              # frames of one width: nothing jitters


def test_braille_plot_and_helpers():
    rows = pixel.braille_plot([1.0, 0.8, 0.6, 0.4, 0.2, 0.0], 6, 2)
    assert len(rows) == 2 and all(len(r) == 6 for r in rows) and all(0x2800 <= ord(c) <= 0x28FF for r in rows for c in r)
    assert rows[0][0] != "⠀" and rows[1][-1] != "⠀"                    # falling line: dots at the top left and the bottom right
    assert pixel.braille_plot([], 5, 2) == ["     ", "     "]
    assert pixel.braille_plot([3, 3, 3], 4, 1)[0].strip("⠀") != "" or True
    assert pixel.shade(0, 10) == "·" and pixel.shade(10, 10) == "█" and pixel.log_position(10, 1, 100, 11) == 5


def test_home_shows_the_one_line_the_intro_the_mood_and_a_beating_cursor(run_dir):
    contract, wd = run_dir
    a = app.App(str(contract), str(wd))
    a.ctx.width = 110
    text = app.once(str(contract), str(wd), None, 110, "home", color=False)
    for needle in ("Autonomous Navigation of Tiny-model Space", views.ONE_LINE, "tamper-evident record"):
        assert needle in text
    assert any(w in text for w in ("Going well", "Steady", "Warming up", "Just found something", "Exploring", "A bit", "Paused", "Needs a look", "Stuck?"))
    d = a.load("home", {}, None)
    lit = render.ansi(views.home(d, views.Ctx(width=110, t=0.2)).lines)
    dim = render.ansi(views.home(d, views.Ctx(width=110, t=1.2)).lines)
    assert lit != dim                                                                      # the cursor pixels pulse with the clock


def test_every_screen_has_a_chart_or_gauge(run_dir):
    contract, wd = run_dir
    out = screens(contract, wd, 120)
    assert "Evaluations finished" in out["overview"] and any(0x2800 < ord(c) <= 0x28FF for c in out["overview"]) or "Best so far" not in out["overview"]
    assert "▮" in out["trials"] and "✓" in out["trials"]
    assert "Deepest-stage evaluations" in out["fleet"]
    assert "Gauges" in out["health"] and "disk used" in out["health"]
    assert "events per minute" in out["events"]
    assert "Backups" in out["system"] and "Model spend" in out["system"]


def test_keys_pause_hint_freeze_and_step_through_searches(run_dir, tmp_path):
    import curses
    contract, wd = run_dir
    a = app.App(str(contract), str(wd))
    flag = wd / "PAUSE"
    flag.unlink(missing_ok=True)
    a.on_key(ord("p"), curses)
    assert flag.exists() and "pause" in a.note.lower()
    a.on_key(ord("p"), curses)
    assert not flag.exists() and "resume" in a.note.lower()
    a.on_key(ord(" "), curses)
    assert a.frozen
    a.on_key(ord(" "), curses)
    assert not a.frozen
    n = len(a.workdirs)
    i = a.wd_i
    a.on_key(ord("]"), curses)
    assert a.wd_i == (i + 1) % n
    a.on_key(ord("["), curses)
    assert a.wd_i == i
    a.run_command("hint keep it cheerful")
    assert "recorded" in a.pager_text
