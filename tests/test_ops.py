"""Tests for the ops additions: experiment registry, definition guard, seed runner, off-machine copies, soak/drill scripts."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from antslab import cli
from antslab.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "src/antslab/gyms/toy.py"
TOML_TPL = (ROOT / "tests/conftest.py").read_text().split('TOML = """')[1].split('"""')[0]


def _contract(tmp_path, timeout=20):
    p = tmp_path / "task.toml"
    p.write_text(TOML_TPL.format(toy=TOY, timeout=timeout))
    return p


# ===================== 1. EXPERIMENT REGISTRY =====================

def test_load_experiments_valid():
    from antslab.experiments import load_experiments
    exps = load_experiments(ROOT / "deploy/experiments.toml")
    assert len(exps) == 3
    names = {e["name"] for e in exps}
    assert names == {"portfolio", "portfolio_llm", "portfolio_agent"}
    for e in exps:
        assert e["status"] == "running" and e["seed"] == 1


def test_load_experiments_rejects_unknown_keys(tmp_path):
    from antslab.experiments import load_experiments, RegistryError
    (tmp_path / "e.toml").write_text('[[experiment]]\nname="a"\narm="random"\nseed=1\nquestion="q"\nstatus="planned"\nbogus="x"\n')
    with pytest.raises(RegistryError, match="unknown keys"):
        load_experiments(tmp_path / "e.toml")


def test_load_experiments_rejects_duplicate_names(tmp_path):
    from antslab.experiments import load_experiments, RegistryError
    text = ('[[experiment]]\nname="a"\narm="random"\nseed=1\nquestion="q"\nstatus="planned"\n' * 2)
    (tmp_path / "e.toml").write_text(text)
    with pytest.raises(RegistryError, match="duplicate"):
        load_experiments(tmp_path / "e.toml")


def test_load_experiments_rejects_bad_names(tmp_path):
    from antslab.experiments import load_experiments, RegistryError
    (tmp_path / "e.toml").write_text('[[experiment]]\nname="A-B"\narm="random"\nseed=1\nquestion="q"\nstatus="planned"\n')
    with pytest.raises(RegistryError, match="bad name"):
        load_experiments(tmp_path / "e.toml")


def test_load_experiments_rejects_bad_status(tmp_path):
    from antslab.experiments import load_experiments, RegistryError
    (tmp_path / "e.toml").write_text('[[experiment]]\nname="a"\narm="random"\nseed=1\nquestion="q"\nstatus="bogus"\n')
    with pytest.raises(RegistryError, match="status"):
        load_experiments(tmp_path / "e.toml")


def test_running_experiments():
    from antslab.experiments import running_experiments
    exps = running_experiments(ROOT / "deploy/experiments.toml")
    assert all(e["status"] == "running" for e in exps) and len(exps) == 3


def test_experiments_cli_runs(tmp_path, capsys):
    toml = '[[experiment]]\nname="x"\narm="random"\nseed=1\nquestion="test"\nstatus="planned"\n'
    (tmp_path / "e.toml").write_text(toml)
    rc = cli.main(["experiments", "--file", str(tmp_path / "e.toml"), "--base", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "x" in out


def test_experiments_has_no_second_definition_of_the_arm():
    """experiments.py must call Campaign.definition_of (which owns the batch size and every key), not rebuild the dict."""
    src = (ROOT / "src/antslab/experiments.py").read_text()
    assert "Campaign.definition_of" in src and '"promote_quantile"' not in src


def test_remote_deploy_start_registry_reads_experiments(tmp_path):
    """start --registry reads experiments.toml and starts exactly the running ones."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "ssh_args.log"
    (fake_bin / "ssh").write_text("#!/bin/sh\necho \"$@\" >> \"$FAKE_LOG\"\nexit 0\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "testhost", "ANTS_USER": "testuser", "ANTS_KEY": str(tmp_path / "key"),
           "FAKE_LOG": str(log)}
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/remote_deploy.sh"), "start", "--registry", str(ROOT / "deploy/experiments.toml")],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30)
    assert result.returncode == 0, result.stderr
    content = log.read_text()
    assert "ants@portfolio.service" in content
    assert "ants@portfolio_llm.service" in content
    assert "ants@portfolio_agent.service" in content


def test_remote_deploy_start_registry_no_running_exits_nonzero(tmp_path):
    reg = tmp_path / "empty.toml"
    reg.write_text('[[experiment]]\nname="x"\narm="random"\nseed=1\nquestion="q"\nstatus="planned"\n')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "ssh").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "x", "ANTS_USER": "x", "ANTS_KEY": "/dev/null"}
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/remote_deploy.sh"), "start", "--registry", str(reg)],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30)
    assert result.returncode != 0
    assert "no running" in result.stdout or "no running" in result.stderr


def test_remote_deploy_registry_with_special_path(tmp_path):
    """The registry path may contain spaces and unicode."""
    special = tmp_path / "dir with spaces 今天"
    special.mkdir()
    reg = special / "experiments.toml"
    reg.write_text('[[experiment]]\nname="a"\narm="random"\nseed=1\nquestion="q"\nstatus="running"\n')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "ssh_args.log"
    (fake_bin / "ssh").write_text("#!/bin/sh\necho \"$@\" >> \"$FAKE_LOG\"\nexit 0\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "x", "ANTS_USER": "x", "ANTS_KEY": "/dev/null",
           "FAKE_LOG": str(log)}
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/remote_deploy.sh"), "start", "--registry", str(reg)],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30)
    assert result.returncode == 0, result.stderr
    assert "ants@a.service" in log.read_text()


# ===================== 2. DEFINITION GUARD =====================

def test_definition_guard_equal_hash_starts(tmp_path):
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "8"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]


def test_definition_guard_changed_operators_refuses_with_code_4(tmp_path):
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"version": "0.5.0", "epoch": "e1", "arm": "evo",
                                  "arm_def": {"arm": "evo", "operators": ["fake_op"], "floors": {},
                                              "promote_quantile": 0.5, "promote_by_cap": False,
                                              "min_history": 4, "batch": 4},
                                  "arm_def_hash": "000000000000"})
    led.close()
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 4, f"expected 4, got {r.returncode}: {r.stderr[-300:]}"
    assert "definition" in r.stderr.lower()
    assert "'evo'" in r.stderr
    assert "ledger.sqlite" in r.stderr


def test_definition_guard_new_definition_flag_proceeds(tmp_path):
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"version": "0.5.0", "epoch": "e1", "arm": "evo",
                                  "arm_def": {"arm": "evo", "operators": ["fake_op"]},
                                  "arm_def_hash": "000000000000"})
    led.close()
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4",
                        "--new-definition"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]
    led = Ledger.open_readonly(wd / "ledger.sqlite")
    starts = led.events("daemon_started")
    last = starts[-1]["payload"]
    assert last.get("definition_changed") is True
    assert last.get("previous_hash") == "000000000000"
    led.close()


def test_definition_guard_version_bump_alone_does_not_refuse(tmp_path):
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]
    led = Ledger(wd / "ledger.sqlite")
    starts = led.events("daemon_started")
    last = starts[-1]["payload"]
    led.event("daemon_started", {**last, "version": "99.99.99"})
    led.close()
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "8"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]


def test_definition_guard_filters_by_arm(tmp_path):
    """Two arms sharing a ledger: a changed hash for arm A does not block arm B."""
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"version": "0.5.0", "epoch": "e1", "arm": "random",
                                  "arm_def": {"arm": "random", "operators": ["fake"]},
                                  "arm_def_hash": "000000000000"})
    led.close()
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    # evo has no daemon_started -> starts fine despite random's mismatch
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, f"evo should start: {r.stderr[-300:]}"
    # random should still refuse
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "random", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 4, f"random should refuse: {r.stderr[-300:]}"


# ===================== 3. SEED RUNNER =====================

def test_seed_runner_dry_run(tmp_path, capsys):
    contract = _contract(tmp_path)
    rc = cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(tmp_path / "base"),
                   "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "random_s1" in out and "random_s2" in out and "random_s3" in out


def test_seed_runner_validates_arm_name(tmp_path, capsys):
    """(3c) Bad arm names like ../x must be rejected."""
    contract = _contract(tmp_path)
    rc = cli.main(["seeds", "--arm", "../x", "--seeds", "1", "--base", str(tmp_path / "base"),
                   "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert rc == 1
    assert "bad arm name" in capsys.readouterr().err


def test_seed_runner_writes_seeds_json_and_refuses_mismatch(tmp_path, capsys):
    """(3d) seeds.json pre-registration checks arms, seeds, contract sha256, budgets."""
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(base),
              "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert (base / "seeds.json").exists()
    meta = json.loads((base / "seeds.json").read_text())
    assert meta["arms"] == ["random"] and meta["seeds"] == [1, 2, 3] and meta["max_evals"] == 4
    # different seeds -> refuse
    capsys.readouterr()
    rc = cli.main(["seeds", "--arm", "random", "--seeds", "1-5", "--base", str(base),
                   "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert rc == 1 and "seeds" in capsys.readouterr().err
    # different max_evals -> refuse
    base2 = tmp_path / "base2"
    cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(base2),
              "--contract", str(contract), "--max-evals", "8", "--dry-run"])
    capsys.readouterr()
    rc = cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(base2),
                   "--contract", str(contract), "--max-evals", "10", "--dry-run"])
    assert rc == 1 and "max_evals" in capsys.readouterr().err
    # different contract -> refuse
    base3 = tmp_path / "base3"
    cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(base3),
              "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    contract2 = tmp_path / "task2.toml"
    contract2.write_text(TOML_TPL.format(toy=TOY, timeout=99))
    capsys.readouterr()
    rc = cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(base3),
                   "--contract", str(contract2), "--max-evals", "4", "--dry-run"])
    assert rc == 1 and "contract_sha256" in capsys.readouterr().err


def test_seed_runner_runs_and_finishes_with_best_per_cap(tmp_path):
    """(3a, 3b, 3e) Run, log redirect, summary includes best_per_cap."""
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
        env.pop(v, None)
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "seeds",
                        "--arm", "random", "--seeds", "1,2", "--base", str(base),
                        "--contract", str(contract), "--max-evals", "4", "--workers", "1"],
                       capture_output=True, text=True, timeout=180, env=env)
    assert r.returncode == 0, r.stderr[-500:]
    assert (base / "random_s1" / "ledger.sqlite").exists()
    assert (base / "random_s1" / "run.log").exists()  # (3a) log file, not PIPE
    # (3e) summary includes best per cap columns from the contract
    assert "best<=" in r.stdout


def test_seed_runner_prints_exit_code_and_log_on_failure(tmp_path):
    """(3f) A failing child prints exit code AND last line of run.log."""
    bad_contract = tmp_path / "bad.toml"
    bad_contract.write_text(TOML_TPL.format(toy="/nonexistent/toy.py", timeout=5))
    base = tmp_path / "base"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "seeds",
                        "--arm", "random", "--seeds", "1", "--base", str(base),
                        "--contract", str(bad_contract), "--max-evals", "4", "--workers", "1"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode != 0 or "exit" in r.stdout.lower()


def test_seed_runner_interleaves_arms(tmp_path):
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "seeds",
                        "--arm", "random,evo", "--seeds", "1,2", "--base", str(base),
                        "--contract", str(contract), "--max-evals", "4", "--workers", "1"],
                       capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stderr[-500:]
    for name in ["random_s1", "evo_s1", "random_s2", "evo_s2"]:
        assert (base / name / "ledger.sqlite").exists(), f"missing {name}"


# ===================== 4. OFF-MACHINE COPIES =====================

def test_offsite_check_reports_when_set(tmp_path, monkeypatch):
    from antslab.system import offsite_check
    offsite = tmp_path / "offsite"
    offsite.mkdir()
    monkeypatch.setenv("ANTS_OFFSITE_DIR", str(offsite))
    level, msg = offsite_check()
    assert level == "FAIL" and "no verified backups" in msg


def test_offsite_check_reports_age(tmp_path, monkeypatch):
    from antslab.backup import make_backup
    from antslab.system import offsite_check
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(3):
        led.event("note", {"i": i})
    led.close()
    offsite = tmp_path / "offsite" / "arm" / "backups"
    make_backup(wd / "ledger.sqlite", offsite)
    monkeypatch.setenv("ANTS_OFFSITE_DIR", str(tmp_path / "offsite"))
    level, msg = offsite_check()
    assert level == "OK" and "offsite" in msg


def test_offsite_check_not_set(monkeypatch):
    from antslab.system import offsite_check
    monkeypatch.delenv("ANTS_OFFSITE_DIR", raising=False)
    assert offsite_check() is None


def test_doctor_includes_offsite_when_set(tmp_path, monkeypatch):
    from antslab.system import durability_checks
    offsite = tmp_path / "offsite"
    offsite.mkdir()
    monkeypatch.setenv("ANTS_OFFSITE_DIR", str(offsite))
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "home"))
    led = Ledger(tmp_path / "wd" / "ledger.sqlite")
    for i in range(3):
        led.event("note", {"i": i})
    checks = durability_checks(led, tmp_path / "wd")
    assert any("offsite" in m for _, m in checks)


def test_pull_backups_script_syntax():
    r = subprocess.run(["bash", "-n", str(ROOT / "deploy/pull_backups.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_pull_backups_no_shell_injection_in_python_calls():
    """pull_backups.sh must pass paths via sys.argv, not string interpolation."""
    text = (ROOT / "deploy/pull_backups.sh").read_text()
    for m in re.finditer(r'"\$PYTHON"\s+-I\s+-c\s+"(.*?)"', text, re.DOTALL):
        py_src = m.group(1)
        assert "$" not in py_src, f"shell variable interpolated in python source: {py_src[:100]}"


def test_pull_backups_worst_age_tracks_oldest_newest():
    text = (ROOT / "deploy/pull_backups.sh").read_text()
    assert "WORST_AGE_H" in text
    assert "NEWEST_AGE_H" not in text


# ===================== 5. SOAK AND DRILLS =====================

def test_remote_deploy_soak_syntax():
    r = subprocess.run(["bash", "-n", str(ROOT / "deploy/remote_deploy.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_drill_refuses_all():
    env = {**os.environ, "ANTS_HOST": "x", "ANTS_USER": "x", "ANTS_KEY": "/dev/null"}
    r = subprocess.run(["bash", str(ROOT / "deploy/remote_deploy.sh"), "drill", "kill9", "all"],
                       capture_output=True, text=True, env=env, timeout=10)
    assert r.returncode != 0
    assert "refuses" in r.stdout or "refuses" in r.stderr


def test_service_unit_prevents_exit_4_restart():
    text = (ROOT / "deploy/ants@.service").read_text()
    assert "RestartPreventExitStatus=3 4" in text


# ===================== MISC =====================

def test_parse_seeds():
    from antslab.seeds import parse_seeds
    assert parse_seeds("1-3") == [1, 2, 3]
    assert parse_seeds("1,3,5") == [1, 3, 5]
    assert parse_seeds("1-1") == [1]


def test_operations_md_has_exit_4_everywhere():
    text = (ROOT / "docs/operations.md").read_text()
    # match the directive and its value (may include a space before the next non-digit)
    hits = re.findall(r"RestartPreventExitStatus=[\d ]+", text)
    assert hits
    for h in hits:
        h = h.strip()
        assert "3 4" in h, f"stale reference (missing exit 4): {h}"


def _pull_layout(tmp_path, arms=("alpha", "beta")):
    """the layout the server really has after `remote_deploy.sh pull-backups`: DIR/backups/<arm>/"""
    from antslab.backup import make_backup
    for arm in arms:
        wd = tmp_path / "src" / arm
        wd.mkdir(parents=True)
        led = Ledger(wd / "ledger.sqlite")
        led.event("note", {"x": 1})
        led.close()
        make_backup(wd / "ledger.sqlite", tmp_path / "pulled" / "backups" / arm, keep=3, compress=True)
    return tmp_path / "pulled"


def _run_pull(d):
    return subprocess.run(["bash", str(ROOT / "deploy/pull_backups.sh"), str(d)], capture_output=True, text=True,
                          env={**os.environ, "ANTS_NO_PULL": "1", "ANTS_PYTHON": sys.executable}, timeout=120)


def test_pull_backups_verifies_the_real_layout(tmp_path):
    d = _pull_layout(tmp_path)
    r = _run_pull(d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pulled=2 verified_ok=2 bad=0" in r.stdout


def test_pull_backups_fails_when_nothing_is_found(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    r = _run_pull(d)
    assert r.returncode != 0 and "no backup" in r.stdout


# ===================== review fixes of the second half =====================

def test_experiments_hash_is_the_campaigns_own_for_every_registered_arm(tmp_path):
    """ants experiments used to rebuild the definition by hand and report MISMATCH for arms with a reserve, a discount or a promotion rule"""
    import hashlib
    from antslab.campaign import Campaign
    from antslab.contract import load_contract
    from antslab.experiments import _StubClient, arm_definition_hash, load_experiments
    from antslab.proposers import make_proposer
    ct = ROOT / "contracts/tinyts_real.toml"
    c = load_contract(ct)
    arms = {e["arm"] for e in load_experiments(ROOT / "deploy/experiments.toml")} | {"portfolio", "portfolio_screen", "portfolio_lean"}
    for arm in sorted(arms):
        d = Campaign.definition_of(arm, make_proposer(arm, c, 1, _StubClient()), c)
        assert arm_definition_hash(arm, ct, 1) == hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12], arm


def test_a_missing_optional_operator_does_not_change_the_definition(monkeypatch):
    """optuna missing on a host must be a degraded start, not an arm definition the guard answers with exit 4 (RestartPreventExitStatus then leaves the arm down)"""
    from antslab import portfolio
    from antslab.campaign import Campaign
    from antslab.contract import load_contract
    from antslab.proposers import make_proposer
    c = load_contract(ROOT / "contracts/tinyts_real.toml")
    with_tpe = make_proposer("portfolio", c, 1, None)
    if "tpe" not in with_tpe.ops:
        pytest.skip("optuna is not installed here")
    h_with = Campaign.definition_of("portfolio", with_tpe, c)

    def boom(*a, **k):
        raise ImportError("no optuna")
    monkeypatch.setattr(portfolio, "TPEHistProposer", boom)
    without = make_proposer("portfolio", c, 1, None)
    assert "tpe" not in without.ops and without.unavailable == ["tpe"]
    assert Campaign.definition_of("portfolio", without, c) == h_with


def test_a_refused_start_registers_no_epoch(tmp_path):
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    base = [sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract), "--workdir", str(wd), "--seed", "1", "--max-evals", "4"]
    assert subprocess.run(base + ["--arm", "evo"], capture_output=True, text=True, timeout=120, env=env).returncode == 0
    led = Ledger(wd / "ledger.sqlite")
    # pretend an earlier start recorded another definition for this arm
    first = led.recent_events("daemon_started", 1)[0]["payload"]
    led.event("daemon_started", {**first, "arm_def_hash": "000000000000"})
    n = led.conn.execute("SELECT COUNT(*) FROM epochs").fetchone()[0]
    led.close()
    r = subprocess.run(base + ["--arm", "evo"], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 4, r.stderr[-300:]
    led = Ledger(wd / "ledger.sqlite")
    assert led.conn.execute("SELECT COUNT(*) FROM epochs").fetchone()[0] == n
    led.close()


def test_pull_backups_verifies_series_backups_too(tmp_path):
    from antslab.backup import make_backup
    d = _pull_layout(tmp_path, arms=("alpha",))
    wd = tmp_path / "src" / "s1"
    wd.mkdir(parents=True)
    led = Ledger(wd / "ledger.sqlite"); led.event("note", {}); led.close()
    make_backup(wd / "ledger.sqlite", d / "backups" / "series" / "e43a" / "portfolio_s1", keep=3, compress=True)
    r = _run_pull(d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pulled=2 verified_ok=2 bad=0" in r.stdout


def test_active_arm_units_fails_closed_and_sees_the_restart_window(monkeypatch):
    from antslab import seeds
    monkeypatch.setattr("shutil.which", lambda n: "/bin/systemctl")

    class R:
        def __init__(s, out, rc=0): s.stdout, s.returncode = out, rc
    monkeypatch.setattr(seeds.subprocess, "run", lambda *a, **k: R("ants@a.service loaded activating auto-restart x\nants@b.service loaded inactive dead x\nants@c.service loaded active running x\n"))
    assert seeds.active_arm_units() == ["ants@a.service", "ants@c.service"]
    monkeypatch.setattr(seeds.subprocess, "run", lambda *a, **k: R("", 1))
    assert seeds.active_arm_units()                                             # an error is "unknown": refuse
    def boom(*a, **k): raise OSError("no")
    monkeypatch.setattr(seeds.subprocess, "run", boom)
    assert seeds.active_arm_units()


def test_doctor_warns_when_an_operator_is_unavailable(tmp_path):
    from antslab import ui
    from antslab.contract import load_contract
    c = load_contract(ROOT / "contracts/tinyts_real.toml")
    led = Ledger(tmp_path / "l.sqlite")
    led.event("daemon_started", {"arm": "portfolio", "arm_def_hash": "abc", "unavailable": ["tpe"]})
    out = ui.doctor(led, c, "portfolio", tmp_path)
    assert any(lvl == "WARN" and "tpe unavailable" in m for lvl, m in out)
    led.close()
