import json
import pytest
from antslab import ui
from antslab.campaign import Campaign
from antslab.proposers import make_proposer, LLMProposer, EvoProposer
import random


@pytest.fixture
def ran(ledger, make_contract, tmp_path):
    c = make_contract()
    cp = Campaign(ledger, c, tmp_path / "w", "evo", make_proposer("evo", c, 3), 3, budget_evals=40)
    cp.run()
    return ledger, c, tmp_path / "w"


def test_snapshot_is_json_serialisable_and_complete(ran):
    led, c, wd = ran
    s = ui.snapshot(led, c, "evo", wd)
    json.dumps(s, default=str)
    assert s["chain_tail_ok"] and s["best_by_cap"] and s["stages"]["s0"]["done"] > 0 and not s["paused"]
    assert "evo" in ui.render_top(s) and "best per parameter cap" in ui.render_top(s)


def test_show_follows_the_lineage_and_reports_unknown_refs(ran):
    led, c, wd = ran
    lb = ui.leaderboard(__import__("antslab.history", fromlist=["History"]).History(led, c, "evo"), c)
    tid = next(iter(lb.values()))[0]["trial"]
    d = ui.show(led, c, str(tid))
    assert d["trial"] == tid and "non_default" in d and isinstance(d["lineage"], list)
    assert "not_found" in ui.show(led, c, "zzzz-no-such") and "not_found" not in ui.show(led, c, str(tid))


def test_hint_reaches_the_llm_prompt_but_only_as_a_suggestion(ledger, make_contract):
    c = make_contract()
    ui.hint(ledger, "try larger b with a small")
    p = LLMProposer(c, None, EvoProposer(c, random.Random(0)), random.Random(0))
    prompt = p.build_prompt([], "", [e["payload"]["text"] for e in ledger.recent_events("user_hint", 3)])
    text = prompt[-1]["content"]
    assert "try larger b with a small" in text and "suggestions, not rules" in text and "allowed keys" in text.lower()
    assert ledger.verify_chain()[0]


def test_pause_flag_idles_the_loop_and_resume_continues(ledger, make_contract, tmp_path):
    c = make_contract()
    cp = Campaign(ledger, c, tmp_path / "w", "random", make_proposer("random", c, 0), 0, budget_evals=5)
    (tmp_path / "w" / ui.PAUSE_FILE).write_text("x")
    assert cp.step() == "paused:user" and not ledger.trials()
    (tmp_path / "w" / ui.PAUSE_FILE).unlink()
    assert cp.step().startswith("ran:")


def test_doctor_flags_real_problems(ran, tmp_path):
    led, c, wd = ran
    assert all(l != "FAIL" for l, _ in ui.doctor(led, c, "evo", wd))
    led.conn.execute("UPDATE events SET payload='{}' WHERE seq=3")          # tamper
    res = ui.doctor(led, c, "evo", wd)
    assert any(l == "FAIL" and "chain" in m for l, m in res)


def test_cli_commands_run_and_every_json_output_parses(ledger, make_contract, tmp_path, capsys):
    from antslab import cli
    make_contract()
    base = ["--contract", str(tmp_path / "task.toml"), "--workdir", str(tmp_path / "cli")]
    assert cli.main(["run", *base, "--arm", "evo", "--max-evals", "12"]) == 0
    capsys.readouterr()
    for cmd in (["best"], ["top", "--once"], ["log", "-n", "5"], ["doctor"]):
        rc = cli.main([cmd[0], *base, "--arm", "evo", *cmd[1:], "--json"])
        out = capsys.readouterr().out.strip()
        parsed = json.loads(out)                                   # raises if a command prints non-JSON under --json
        assert rc in (0, 2) and parsed
    assert cli.main(["hint", *base, "hello"]) == 0 and cli.main(["pause", *base]) == 0 and cli.main(["resume", *base]) == 0
    capsys.readouterr()
    assert cli.main(["show", *base, "1", "--json"]) == 0 and json.loads(capsys.readouterr().out)["trial"] == 1


def test_interactive_shell_runs_commands_and_survives_typos(ledger, make_contract, tmp_path, monkeypatch, capsys):
    import io
    from antslab import cli
    make_contract()
    base = ["--contract", str(tmp_path / "task.toml"), "--workdir", str(tmp_path / "sh")]
    assert cli.main(["run", *base, "--arm", "evo", "--max-evals", "10"]) == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO("best --top 2\nbogus\nhint look at longer windows\nshow 1\ndoctor\npause\nresume\nquit\n"))
    assert cli.main(["shell", *base, "--arm", "evo"]) == 0
    out = capsys.readouterr().out
    assert "cap <=" in out and "unknown command 'bogus'" in out and "recorded (hint" in out and "[  OK] hash chain intact" in out
    assert "paused" in out and "resumed" in out


def test_flags_are_optional_and_resolution_order_is_explicit_then_env_then_autodetect(tmp_path, monkeypatch, capsys):
    """Regression: `ants shell` without flags used to fail with 'required: --contract, --workdir'."""
    import argparse
    from antslab import cli
    toml = tmp_path / "task.toml"
    from tests.conftest import TOML, TOY
    toml.write_text(TOML.format(toy=TOY, timeout=20))
    w = tmp_path / "wd"
    assert cli.main(["run", "--contract", str(toml), "--workdir", str(w), "--arm", "evo", "--max-evals", "6"]) == 0
    capsys.readouterr()
    for k in ("ANTS_CONTRACT", "ANTS_WORKDIR", "ANTS_ARM"):
        monkeypatch.delenv(k, raising=False)
    # env only: the arm is inferred from the ledger
    monkeypatch.setenv("ANTS_CONTRACT", str(toml)); monkeypatch.setenv("ANTS_WORKDIR", str(w))
    a = cli._resolve(argparse.Namespace(contract=None, workdir=None, arm=None, json=False))
    assert a.contract == str(toml) and a.workdir == str(w) and a.arm == "evo"
    assert "using" in capsys.readouterr().err                              # nothing is guessed silently
    # an explicit flag beats the environment
    b = cli._resolve(argparse.Namespace(contract=str(toml), workdir=str(tmp_path / "other"), arm="qd", json=True))
    assert b.workdir == str(tmp_path / "other") and b.arm == "qd" and capsys.readouterr().err == ""
    # nothing configured anywhere -> a clear message, not a stack trace
    monkeypatch.delenv("ANTS_CONTRACT"); monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        cli._resolve(argparse.Namespace(contract=None, workdir=None, arm=None, json=False))
    assert "no contract found" in str(e.value)


def test_show_is_human_readable_by_default(ran, capsys):
    from antslab import cli
    led, c, wd = ran
    import os
    tid = led.conn.execute("SELECT id FROM trials WHERE state='done' AND stage='s1' LIMIT 1").fetchone()
    tid = tid[0] if tid else 1
    txt = ui.render_show(ui.show(led, c, str(tid)))
    assert txt.startswith(f"trial {tid}") and "non-default levers" in txt and "{" not in txt.splitlines()[0]
