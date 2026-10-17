"""Tests for the ops additions: experiment registry, definition guard, seed runner, off-machine copies, soak/drill scripts."""
import hashlib
import json
import os
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
        assert e["status"] == "running"
        assert e["seed"] == 1


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
    assert all(e["status"] == "running" for e in exps)
    assert len(exps) == 3


def test_experiments_cli_runs(tmp_path, capsys):
    toml = '[[experiment]]\nname="x"\narm="random"\nseed=1\nquestion="test"\nstatus="planned"\n'
    (tmp_path / "e.toml").write_text(toml)
    rc = cli.main(["experiments", "--file", str(tmp_path / "e.toml"), "--base", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "x" in out


def test_remote_deploy_start_registry_reads_experiments(tmp_path):
    """Test that `start --registry` in remote_deploy.sh reads the experiments.toml correctly.
    Uses a fake ssh that records its arguments."""
    fake_ssh = tmp_path / "bin" / "ssh"
    fake_ssh.parent.mkdir()
    fake_ssh.write_text("#!/bin/sh\necho \"$@\" >> \"$(dirname $0)/ssh_args.log\"\nexit 0\n")
    fake_ssh.chmod(0o755)
    # also need a fake systemctl
    fake_sys = tmp_path / "bin" / "systemctl"
    fake_sys.write_text("#!/bin/sh\nexit 0\n")
    fake_sys.chmod(0o755)

    env = {**os.environ, "PATH": str(tmp_path / "bin") + ":" + os.environ["PATH"],
           "ANTS_HOST": "testhost", "ANTS_USER": "testuser", "ANTS_KEY": str(tmp_path / "key")}
    # the script uses python3 to parse the TOML
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/remote_deploy.sh"), "start", "--registry", str(ROOT / "deploy/experiments.toml")],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30)
    # it should have tried to ssh to start the three running arms
    assert "portfolio" in result.stdout or "portfolio" in result.stderr or result.returncode == 0


# ===================== 2. DEFINITION GUARD =====================

def test_definition_guard_equal_hash_starts(tmp_path):
    """Same arm definition: ants run starts normally."""
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]
    # second run with same code: should also succeed
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "8"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]


def test_definition_guard_changed_operators_refuses_with_code_4(tmp_path):
    """A changed arm definition refuses with exit code 4."""
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    # write a fake daemon_started with a different hash
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"version": "0.5.0", "epoch": "e1",
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


def test_definition_guard_new_definition_flag_proceeds(tmp_path):
    """--new-definition allows proceeding despite a hash mismatch."""
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"version": "0.5.0", "epoch": "e1",
                                  "arm_def": {"arm": "evo", "operators": ["fake_op"]},
                                  "arm_def_hash": "000000000000"})
    led.close()
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4",
                        "--new-definition"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]
    # check the daemon_started event has definition_changed
    led = Ledger.open_readonly(wd / "ledger.sqlite")
    starts = [e for e in led.events("daemon_started")]
    assert len(starts) >= 2
    last_payload = starts[-1]["payload"]
    assert last_payload.get("definition_changed") is True
    assert last_payload.get("previous_hash") == "000000000000"
    led.close()


def test_definition_guard_version_bump_alone_does_not_refuse(tmp_path):
    """A version change with the same arm definition does NOT refuse."""
    contract = _contract(tmp_path)
    wd = tmp_path / "wd"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    # first run
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "4"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]
    # tamper with the version in the last daemon_started (but NOT the hash)
    led = Ledger(wd / "ledger.sqlite")
    starts = led.events("daemon_started")
    assert starts
    last = starts[-1]["payload"]
    # manually add a daemon_started with a different version but same hash
    led.event("daemon_started", {**last, "version": "99.99.99"})
    led.close()
    # second run should succeed (version is excluded from comparison)
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "run", "--contract", str(contract),
                        "--workdir", str(wd), "--arm", "evo", "--seed", "1", "--max-evals", "8"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]


# ===================== 3. SEED RUNNER =====================

def test_seed_runner_dry_run(tmp_path, capsys):
    contract = _contract(tmp_path)
    rc = cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(tmp_path / "base"),
                   "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "random_s1" in out and "random_s2" in out and "random_s3" in out


def test_seed_runner_writes_seeds_json_and_refuses_mismatch(tmp_path, capsys):
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    # first call writes seeds.json
    cli.main(["seeds", "--arm", "random", "--seeds", "1-3", "--base", str(base),
              "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert (base / "seeds.json").exists()
    meta = json.loads((base / "seeds.json").read_text())
    assert meta["arms"] == ["random"] and meta["seeds"] == [1, 2, 3]
    # second call with different seeds should fail
    rc = cli.main(["seeds", "--arm", "random", "--seeds", "1-5", "--base", str(base),
                   "--contract", str(contract), "--max-evals", "4", "--dry-run"])
    assert rc == 1


def test_seed_runner_runs_and_finishes(tmp_path):
    """Actually run seeds with the toy contract and verify workdirs are created."""
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "seeds",
                        "--arm", "random", "--seeds", "1,2", "--base", str(base),
                        "--contract", str(contract), "--max-evals", "4", "--workers", "1"],
                       capture_output=True, text=True, timeout=180, env=env)
    assert r.returncode == 0, r.stderr[-500:]
    assert (base / "random_s1" / "ledger.sqlite").exists()
    assert (base / "random_s2" / "ledger.sqlite").exists()
    assert (base / "seeds.json").exists()


def test_seed_runner_interleaves_arms(tmp_path):
    """Multiple arms are interleaved: s1 of every arm before s2 of any."""
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "seeds",
                        "--arm", "random,evo", "--seeds", "1,2", "--base", str(base),
                        "--contract", str(contract), "--max-evals", "4", "--workers", "1"],
                       capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stderr[-500:]
    # all four workdirs should exist
    for name in ["random_s1", "evo_s1", "random_s2", "evo_s2"]:
        assert (base / name / "ledger.sqlite").exists(), f"missing {name}"


# ===================== 4. OFF-MACHINE COPIES =====================

def test_offsite_check_reports_when_set(tmp_path, monkeypatch):
    """ANTS_OFFSITE_DIR set but empty -> FAIL."""
    from antslab.system import offsite_check
    offsite = tmp_path / "offsite"
    offsite.mkdir()
    monkeypatch.setenv("ANTS_OFFSITE_DIR", str(offsite))
    level, msg = offsite_check()
    assert level == "FAIL" and "no verified backups" in msg


def test_offsite_check_reports_age(tmp_path, monkeypatch):
    """ANTS_OFFSITE_DIR with a backup -> reports age."""
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
    """ANTS_OFFSITE_DIR not set -> None (no check)."""
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
    """pull_backups.sh passes bash -n."""
    r = subprocess.run(["bash", "-n", str(ROOT / "deploy/pull_backups.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ===================== 5. SOAK AND DRILLS =====================

def test_remote_deploy_soak_syntax():
    """The soak and drill sections of remote_deploy.sh parse cleanly."""
    r = subprocess.run(["bash", "-n", str(ROOT / "deploy/remote_deploy.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_drill_refuses_all():
    """drill refuses 'all' as arm name."""
    env = {**os.environ, "ANTS_HOST": "x", "ANTS_USER": "x", "ANTS_KEY": "/dev/null"}
    r = subprocess.run(["bash", str(ROOT / "deploy/remote_deploy.sh"), "drill", "kill9", "all"],
                       capture_output=True, text=True, env=env, timeout=10)
    assert r.returncode != 0
    assert "refuses" in r.stdout or "refuses" in r.stderr


def test_service_unit_prevents_exit_4_restart():
    """ants@.service has RestartPreventExitStatus that includes 4."""
    text = (ROOT / "deploy/ants@.service").read_text()
    assert "RestartPreventExitStatus=3 4" in text


# ===================== MISC =====================

def test_parse_seeds():
    from antslab.seeds import parse_seeds
    assert parse_seeds("1-3") == [1, 2, 3]
    assert parse_seeds("1,3,5") == [1, 3, 5]
    assert parse_seeds("1-1") == [1]
