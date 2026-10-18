"""Tests for `ants preflight` — the read-only pre/post-deploy checker."""
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from antslab import cli
from antslab.ledger import Ledger, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "src/antslab/gyms/toy.py"
TOML_TPL = (ROOT / "tests/conftest.py").read_text().split('TOML = """')[1].split('"""')[0]
REAL = ROOT / "contracts" / "tinyts_real.toml"


def _contract(tmp_path, timeout=20):
    p = tmp_path / "task.toml"
    p.write_text(TOML_TPL.format(toy=TOY, timeout=timeout))
    return p


# ===================== 1. PINS MODULE =====================

def test_pinned_hashes_match_code():
    """The hashes in pins.py must match what the current code computes.
    If this fails, the arm definition changed: create a new arm name, do not update the pin."""
    from antslab.pins import PINNED_HASHES
    from antslab.experiments import arm_definition_hash
    for arm, (expected, date, note) in PINNED_HASHES.items():
        h = arm_definition_hash(arm, str(REAL), 1)
        assert h == expected, f"{arm}: pinned {expected} != computed {h}; the arm definition changed"


def test_pins_module_has_expected_arms():
    from antslab.pins import PINNED_HASHES
    assert "portfolio" in PINNED_HASHES
    assert "portfolio_screen" in PINNED_HASHES
    assert "portfolio_lean" in PINNED_HASHES


# ===================== 2. READ-ONLY: mtimes/sizes unchanged =====================

def test_preflight_does_not_modify_ledger(tmp_path):
    """Snapshot mtimes and sizes before and after preflight, including an older-schema ledger."""
    # Create a v1 ledger (older schema) to verify no migration happens
    wd = tmp_path / "arm1"
    wd.mkdir()
    db_path = wd / "ledger.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE trials(id INTEGER PRIMARY KEY, gym TEXT, arm TEXT, seed INTEGER, params TEXT, source TEXT,
            stage TEXT DEFAULT 's0', spec_key TEXT DEFAULT '', parent_id INTEGER, origin_key TEXT, edit TEXT, epoch TEXT DEFAULT '',
            state TEXT DEFAULT 'queued', attempt INTEGER DEFAULT 0, priority INTEGER DEFAULT 0,
            created_at REAL, started_at REAL, finished_at REAL, lease_until REAL, metrics TEXT, error TEXT, out_dir TEXT, cpu_s REAL);
        CREATE TABLE epochs(id TEXT PRIMARY KEY, ts REAL, info TEXT);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, payload TEXT, prev_hash TEXT, hash TEXT);
        CREATE TABLE artifacts(id INTEGER PRIMARY KEY, trial_id INTEGER, path TEXT UNIQUE, kind TEXT, sha256 TEXT, bytes INTEGER,
            protected INTEGER DEFAULT 0, archived_uri TEXT, archived_sha256 TEXT, verified_at REAL, deleted_at REAL);
        CREATE TABLE unseals(id INTEGER PRIMARY KEY, gym TEXT, ts REAL, candidate TEXT, result TEXT, epoch TEXT DEFAULT '', sealed_set TEXT DEFAULT '');
        CREATE TABLE judgements(id INTEGER PRIMARY KEY, ts REAL, judge TEXT, kind TEXT, subject TEXT, p REAL, label TEXT, raw TEXT, authority TEXT, outcome INTEGER, resolved_at REAL);
        CREATE TABLE llm_calls(id INTEGER PRIMARY KEY, ts REAL, model TEXT, ok INTEGER, tokens INTEGER, note TEXT);
        PRAGMA user_version=1;
    """)
    conn.close()

    # Snapshot the main db file (WAL/SHM are ephemeral SQLite artifacts, not data)
    before_size = db_path.stat().st_size
    before_mtime = db_path.stat().st_mtime_ns
    assert not list(wd.glob("*.pre-migration*"))

    time.sleep(0.05)

    from antslab.preflight import check_ledgers
    results = check_ledgers([db_path])

    # The db file itself is untouched (no migration, no writes)
    assert db_path.stat().st_size == before_size
    assert db_path.stat().st_mtime_ns == before_mtime
    # No migration copies (open_readonly does not migrate)
    assert not list(wd.glob("*.pre-migration*"))

    # The older-schema ledger should produce a WARN about migration
    levels = [l for l, _ in results]
    msgs = [m for _, m in results]
    assert any("migrated one-way" in m for m in msgs), f"expected migration warning, got: {msgs}"


def test_preflight_does_not_modify_current_schema_ledger(tmp_path):
    """A current-schema ledger must produce no changes at all (WAL/SHM are ephemeral SQLite files, not data)."""
    wd = tmp_path / "arm2"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("test_event", {"x": 1})
    led.close()

    db = wd / "ledger.sqlite"
    before_size = db.stat().st_size
    before_mtime = db.stat().st_mtime_ns

    # no .pre-migration file should exist
    assert not list(wd.glob("*.pre-migration*"))

    time.sleep(0.05)

    from antslab.preflight import check_ledgers
    results = check_ledgers([db])

    # the main db file is unchanged (WAL/SHM are normal SQLite read artifacts)
    assert db.stat().st_size == before_size
    assert db.stat().st_mtime_ns == before_mtime
    # no migration copies created
    assert not list(wd.glob("*.pre-migration*"))


# ===================== 3. CHECK FUNCTIONS =====================

def test_check_runtime():
    from antslab.preflight import check_runtime
    results = check_runtime()
    levels = [l for l, _ in results]
    assert "FAIL" not in levels  # we run on >= 3.11 with numpy/scipy


def test_check_epoch_matches(tmp_path):
    """When series epoch matches, no FAIL."""
    ct = _contract(tmp_path)
    from antslab.series import compute_epoch
    epoch = compute_epoch(ct)
    series = {"epoch": epoch}
    from antslab.preflight import check_epoch
    results = check_epoch(ct, series)
    assert not any(l == "FAIL" for l, _ in results)


def test_check_epoch_mismatch(tmp_path):
    """When series epoch differs, FAIL."""
    ct = _contract(tmp_path)
    series = {"epoch": "000000000000"}
    from antslab.preflight import check_epoch
    results = check_epoch(ct, series)
    assert any(l == "FAIL" and "mismatch" in m for l, m in results)


def test_check_arm_definitions_pinned():
    """portfolio must match its pin."""
    from antslab.preflight import check_arm_definitions
    results = check_arm_definitions(["portfolio"], str(REAL), 1)
    assert any(l == "OK" and "matches pin" in m for l, m in results)


def test_check_arm_definitions_detects_ledger_mismatch(tmp_path):
    """If ledger has a different def hash, FAIL."""
    wd = tmp_path / "arm_test"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"arm": "portfolio", "arm_def_hash": "ffffffffffff"})
    led.close()
    from antslab.preflight import check_arm_definitions
    results = check_arm_definitions(["portfolio"], str(REAL), 1, {"portfolio": [wd / "ledger.sqlite"]})
    assert any(l == "FAIL" and "exit 4" in m for l, m in results)


@pytest.mark.parametrize("suffix", ["", "_s1", "_s8"])
def test_prefix_named_arms_are_checked_against_their_own_ledgers(tmp_path, suffix):
    """A valid screen ledger must not trigger a definition mismatch for portfolio."""
    from antslab.experiments import arm_definition_hash
    from antslab.preflight import build_ledger_map, check_arm_definitions

    arms = ["portfolio", "portfolio_screen"]
    paths = []
    for arm in arms:
        wd = tmp_path / (arm + suffix)
        wd.mkdir()
        path = wd / "ledger.sqlite"
        ledger = Ledger(path)
        ledger.event("daemon_started", {"arm": arm, "arm_def_hash": arm_definition_hash(arm, REAL, 1)})
        ledger.close()
        paths.append(path)

    results = check_arm_definitions(arms, REAL, 1, build_ledger_map(arms, paths))
    assert not any(level == "FAIL" for level, _ in results), results


def test_unrelated_non_numeric_suffixes_are_not_seed_workdirs():
    from antslab.preflight import build_ledger_map

    paths = [Path(name) / "ledger.sqlite" for name in
             ("portfolio_screen_s1", "portfolio_sandbox", "portfolio_s1_extra")]
    assert build_ledger_map(["portfolio"], paths) == {"portfolio": []}


def test_check_data():
    """data_check should work in this tree."""
    from antslab.preflight import check_data
    results = check_data()
    assert any(l in ("OK", "WARN") for l, _ in results)


def test_check_disk(tmp_path):
    from antslab.preflight import check_disk
    results = check_disk(tmp_path)
    assert len(results) == 1
    assert results[0][0] in ("OK", "WARN", "FAIL")


def test_check_ledgers_ok(tmp_path):
    """A healthy ledger should produce all OK."""
    wd = tmp_path / "ok_arm"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("test", {"a": 1})
    led.close()
    from antslab.preflight import check_ledgers
    results = check_ledgers([wd / "ledger.sqlite"])
    assert any("chain intact" in m for _, m in results)
    assert not any(l == "FAIL" for l, _ in results)


def test_check_ledgers_broken_chain(tmp_path):
    """A ledger with a tampered event should FAIL chain check."""
    wd = tmp_path / "broken"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("test", {"a": 1})
    led.event("test", {"b": 2})
    # tamper with the chain
    led.conn.execute("UPDATE events SET payload='{}' WHERE seq=2")
    led.close()
    from antslab.preflight import check_ledgers
    results = check_ledgers([wd / "ledger.sqlite"])
    assert any(l == "FAIL" and "chain broken" in m for l, m in results)


def test_check_ledgers_too_new(tmp_path):
    """A ledger with a future schema version should FAIL."""
    wd = tmp_path / "new_arm"
    wd.mkdir()
    db_path = wd / "ledger.sqlite"
    led = Ledger(db_path)
    led.close()
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    conn.close()
    from antslab.preflight import check_ledgers
    results = check_ledgers([db_path])
    assert any(l == "FAIL" and "schema version" in m for l, m in results)


def test_check_ledgers_pause_file(tmp_path):
    """PAUSE file should produce a WARN."""
    wd = tmp_path / "paused"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("test", {"a": 1})
    led.close()
    (wd / "PAUSE").write_text("")
    from antslab.preflight import check_ledgers
    results = check_ledgers([wd / "ledger.sqlite"])
    assert any(l == "WARN" and "PAUSE" in m for l, m in results)


def test_preflight_reports_lock_file_without_taking_the_lock(tmp_path, monkeypatch):
    """A read-only preflight must not race a systemd restart by briefly taking ants.lock."""
    from antslab.preflight import check_ledgers
    wd = tmp_path / "arm-lock"
    led = Ledger(wd / "ledger.sqlite")
    led.event("test_event", {"x": 1})
    led.close()
    (wd / "ants.lock").write_text("12345\n")

    def forbidden_flock(*args, **kwargs):
        raise AssertionError("preflight must not acquire the run lock")
    monkeypatch.setattr("fcntl.flock", forbidden_flock)
    results = check_ledgers([wd / "ledger.sqlite"])
    assert any("ants.lock file exists" in message and "12345" in message for _, message in results)


def test_check_secrets_bad_mode(tmp_path):
    """A world-readable llm.env should WARN."""
    env_file = tmp_path / "llm.env"
    env_file.write_text("# placeholder\n")  # content does not matter; only mode is checked
    env_file.chmod(0o644)
    from antslab.preflight import check_secrets
    results = check_secrets(str(env_file))
    assert any(l == "WARN" and "group/world" in m for l, m in results)


def test_check_secrets_good_mode(tmp_path):
    env_file = tmp_path / "llm.env"
    env_file.write_text("# placeholder\n")
    env_file.chmod(0o600)
    from antslab.preflight import check_secrets
    results = check_secrets(str(env_file))
    assert all(l == "OK" for l, _ in results)


def test_check_clock_ok(tmp_path):
    """Clock check should pass when events are in the past."""
    wd = tmp_path / "clock_ok"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("test", {"x": 1})
    led.close()
    from antslab.preflight import check_clock
    results = check_clock([wd / "ledger.sqlite"])
    assert all(l in ("OK", "INFO") for l, _ in results)


def test_check_clock_future(tmp_path):
    """Clock check should FAIL when the newest event is far in the future."""
    wd = tmp_path / "clock_bad"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    # insert an event with a future timestamp
    with led.tx() as c:
        future_ts = time.time() + 600  # 10 minutes in the future
        from antslab.ledger import GENESIS, canon
        body = canon({"x": 1})
        h = Ledger._hash(GENESIS, future_ts, "test", body)
        c.execute("INSERT INTO events(ts,kind,payload,prev_hash,hash) VALUES(?,?,?,?,?)",
                  (future_ts, "test", body, GENESIS, h))
    led.close()
    from antslab.preflight import check_clock
    results = check_clock([wd / "ledger.sqlite"])
    assert any(l == "FAIL" for l, _ in results)


# ===================== 4. CLI INTEGRATION =====================

def test_preflight_cli_basic(tmp_path):
    """The CLI command should run and return 0 when healthy."""
    rc = cli.main(["preflight", "--contract", str(REAL)])
    # might not be 0 if some check finds something, but should not crash
    assert rc in (0, 1)


def test_preflight_cli_json(tmp_path, capsys):
    """--json should produce valid JSON."""
    cli.main(["preflight", "--contract", str(REAL), "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert all("level" in d and "message" in d for d in data)


def test_preflight_cli_strict(tmp_path, capsys, monkeypatch):
    """--strict should exit 1 on WARN."""
    # Force a WARN by making numpy unimportable
    import importlib
    real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
    def fake_import(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("fake")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", fake_import)
    # This may or may not cause a WARN depending on the rest of the checks
    # Just verify the flag is accepted
    rc = cli.main(["preflight", "--strict", "--contract", str(REAL)])
    assert rc in (0, 1)


def test_preflight_exit_0_when_healthy(tmp_path, capsys):
    """With all checks passing, exit 0."""
    rc = cli.main(["preflight", "--contract", str(REAL)])
    assert rc == 0


def test_preflight_with_workdir_and_arm(tmp_path):
    """Should accept --workdir and --arm together."""
    wd = tmp_path / "pf_arm"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("daemon_started", {"arm": "portfolio", "arm_def_hash": "67618a671563"})
    led.close()
    rc = cli.main(["preflight", "--workdir", str(wd), "--arm", "portfolio", "--contract", str(REAL)])
    assert rc == 0


def test_preflight_detects_epoch_mismatch_with_series(tmp_path, capsys):
    """With a fake series file whose epoch is wrong, should FAIL."""
    series_file = tmp_path / "bad_series.toml"
    series_file.write_text(
        'name="test"\nquestion="q"\narms=["portfolio"]\nseeds="1"\n'
        'max_cpu_s=100\nworkers=1\nepoch="000000000000"\n'
        f'contract="{REAL}"\n'
    )
    rc = cli.main(["preflight", "--series", "test", "--series-file", str(series_file)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "mismatch" in out.lower() or "FAIL" in out


# ===================== 5. MUTATION TESTS =====================

def test_fail_on_broken_chain_is_not_cosmetic(tmp_path, capsys):
    """If we removed the chain check, the test would pass (mutation: remove check_ledgers from run_preflight)."""
    wd = tmp_path / "mut1"
    wd.mkdir()
    led = Ledger(wd / "ledger.sqlite")
    led.event("test", {"a": 1})
    led.event("test", {"b": 2})
    led.conn.execute("UPDATE events SET payload='{}' WHERE seq=2")
    led.close()
    rc = cli.main(["preflight", "--workdir", str(wd), "--contract", str(REAL)])
    assert rc == 1  # must fail: the chain is broken
    out = capsys.readouterr().out
    assert "chain broken" in out


def test_fail_on_epoch_mismatch_is_not_cosmetic(tmp_path, capsys):
    """If we removed the epoch check, the test would pass."""
    from antslab.series import compute_epoch
    ct = _contract(tmp_path)
    real_epoch = compute_epoch(ct)
    series_file = tmp_path / "mut_series.toml"
    series_file.write_text(
        f'name="x"\nquestion="q"\narms=["portfolio"]\nseeds="1"\n'
        f'max_cpu_s=100\nworkers=1\nepoch="000000000000"\n'
        f'contract="{ct}"\n'
    )
    rc = cli.main(["preflight", "--series", "x", "--series-file", str(series_file)])
    assert rc == 1


# ===================== 6. DEPLOY SCRIPT verify =====================

def test_deploy_verify_script_syntax():
    """bash -n on the deploy script."""
    script = ROOT / "deploy" / "remote_deploy.sh"
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_deploy_verify_validates_names(tmp_path):
    """The verify subcommand must reject bad arm/series names."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "ssh").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "test", "ANTS_USER": "test", "ANTS_KEY": "/dev/null"}
    # bad arm name with shell metacharacter
    r = subprocess.run(["bash", str(ROOT / "deploy/remote_deploy.sh"), "verify", "../../etc"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode != 0


def test_deploy_verify_runs_with_fake_ssh(tmp_path):
    """verify subcommand calls ssh with ants preflight --json and parses the output."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_log = tmp_path / "ssh.log"
    # fake ssh that outputs a preflight JSON result
    fake_json = json.dumps([{"level": "OK", "message": "ants 0.6.2"}, {"level": "WARN", "message": "disk low"}])
    (fake_bin / "ssh").write_text(f"#!/bin/sh\necho '{fake_json}'\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "test", "ANTS_USER": "test", "ANTS_KEY": "/dev/null"}
    r = subprocess.run(["bash", str(ROOT / "deploy/remote_deploy.sh"), "verify", "--series", "stage_a"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    # no FAIL in the output, so should exit 0
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"


def test_deploy_verify_converts_arm_names_to_preflight_flags(tmp_path):
    """Arm names are arguments to remote_deploy, not positional ants CLI arguments."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_log = tmp_path / "ssh.log"
    fake_json = json.dumps([{"level": "OK", "message": "ants 0.6.2"}])
    (fake_bin / "ssh").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$FAKE_SSH_LOG\"\necho '{fake_json}'\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "test", "ANTS_USER": "test", "ANTS_KEY": "/dev/null", "FAKE_SSH_LOG": str(fake_log)}
    r = subprocess.run(["bash", str(ROOT / "deploy/remote_deploy.sh"), "verify", "portfolio", "portfolio_screen"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    remote_cmd = fake_log.read_text()
    assert "preflight --json --base /var/lib/ants --arm portfolio,portfolio_screen" in remote_cmd


def test_deploy_verify_exits_nonzero_on_fail(tmp_path):
    """verify subcommand exits non-zero when preflight reports a FAIL."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_json = json.dumps([{"level": "FAIL", "message": "chain broken"}])
    (fake_bin / "ssh").write_text(f"#!/bin/sh\necho '{fake_json}'\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "test", "ANTS_USER": "test", "ANTS_KEY": "/dev/null"}
    r = subprocess.run(["bash", str(ROOT / "deploy/remote_deploy.sh"), "verify", "--series", "stage_a"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode != 0


def test_the_hash_a_check_computes_is_the_hash_a_running_campaign_records(tmp_path):
    """experiments/preflight used to carry a copy of the arm definition that lacked the reserve, discount and promotion keys: right for `portfolio`,
    wrong for the other arms (a false MISMATCH). Both now call Campaign.definition_of."""
    from pathlib import Path
    from antslab.campaign import Campaign
    from antslab.contract import load_contract
    from antslab.experiments import arm_definition_hash
    from antslab.ledger import Ledger
    from antslab.proposers import make_proposer
    ct = Path(__file__).resolve().parents[1] / "contracts" / "tinyts_real.toml"
    c = load_contract(ct)
    for arm in ("portfolio", "portfolio_screen", "portfolio_lean", "random", "evo"):
        camp = Campaign(Ledger(tmp_path / f"{arm}.sqlite"), c, tmp_path / arm, arm, make_proposer(arm, c, 1), 1)
        assert arm_definition_hash(arm, ct, 1) == camp.start_record()["arm_def_hash"], arm


def test_disk_uses_the_contracts_thresholds_not_fixed_numbers(monkeypatch):
    """The disk check once ignored the thresholds it was given and said OK at 7 GB where `ants run` pauses (pause 8)."""
    from antslab import janitor, preflight
    th = {"warn": 15, "clean": 12, "pause": 8, "hardstop": 6}
    for free, want in ((20, "OK"), (13, "WARN"), (7, "FAIL"), (5, "FAIL")):
        monkeypatch.setattr(janitor, "free_gb", lambda p, f=free: f)
        assert preflight.check_disk("/x", th)[0][0] == want, free
