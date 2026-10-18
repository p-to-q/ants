"""Tests for the series registry, runner, epoch check, bundle make/verify, and deploy additions."""
import gzip
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


# ===================== 1. SERIES REGISTRY =====================

def test_load_series_valid():
    from antslab.series import load_series
    s = load_series(ROOT / "tests/fixtures/series_stage_a.toml")
    assert s["name"] == "stage_a"
    assert s["arms"] == ["portfolio", "portfolio_screen", "portfolio_lean"]
    assert s["seeds_parsed"] == list(range(1, 9))
    assert s["max_cpu_s"] == 28800.0
    assert s["workers"] == 2
    assert re.match(r"^[0-9a-f]{12}$", s["epoch"])


def test_load_series_rejects_unknown_keys(tmp_path):
    from antslab.series import load_series, SeriesError
    p = tmp_path / "bad.toml"
    p.write_text('name="x"\nquestion="q"\narms=["a"]\nseeds="1"\nmax_cpu_s=100\nworkers=1\nepoch="aabbccddeeff"\nbogus=1\n')
    with pytest.raises(SeriesError, match="unknown keys"):
        load_series(p)


def test_load_series_rejects_missing_keys(tmp_path):
    from antslab.series import load_series, SeriesError
    p = tmp_path / "bad.toml"
    p.write_text('name="x"\n')
    with pytest.raises(SeriesError, match="missing keys"):
        load_series(p)


def test_load_series_rejects_bad_arm(tmp_path):
    from antslab.series import load_series, SeriesError
    p = tmp_path / "bad.toml"
    p.write_text('name="x"\nquestion="q"\narms=["../x"]\nseeds="1"\nmax_cpu_s=100\nworkers=1\nepoch="aabbccddeeff"\n')
    with pytest.raises(SeriesError, match="bad arm"):
        load_series(p)


def test_load_series_rejects_bad_epoch(tmp_path):
    from antslab.series import load_series, SeriesError
    p = tmp_path / "bad.toml"
    p.write_text('name="x"\nquestion="q"\narms=["a"]\nseeds="1"\nmax_cpu_s=100\nworkers=1\nepoch="short"\n')
    with pytest.raises(SeriesError, match="epoch"):
        load_series(p)


def test_load_series_rejects_bad_status(tmp_path):
    from antslab.series import load_series, SeriesError
    p = tmp_path / "bad.toml"
    p.write_text('name="x"\nquestion="q"\narms=["a"]\nseeds="1"\nmax_cpu_s=100\nworkers=1\nepoch="aabbccddeeff"\nstatus="bogus"\n')
    with pytest.raises(SeriesError, match="status"):
        load_series(p)


def test_load_series_rejects_workers_out_of_range(tmp_path):
    from antslab.series import load_series, SeriesError
    p = tmp_path / "bad.toml"
    p.write_text('name="x"\nquestion="q"\narms=["a"]\nseeds="1"\nmax_cpu_s=100\nworkers=9\nepoch="aabbccddeeff"\n')
    with pytest.raises(SeriesError, match="workers"):
        load_series(p)


# ===================== 2. EPOCH CHECK =====================

def test_compute_epoch_matches_campaign():
    """The standalone compute_epoch must produce the same epoch as Campaign._epoch."""
    from antslab.series import compute_epoch
    # compute via the Campaign path
    from antslab.contract import load_contract, code_fingerprint
    c = load_contract(ROOT / "contracts/tinyts_real.toml")
    code_fp = code_fingerprint(c.code_paths)[:16]
    contract_hash = hashlib.sha256(json.dumps(c.raw, sort_keys=True, default=str).encode()).hexdigest()[:16]
    data_hash = ""
    cand = Path(c.code_paths[0]).parents[3] / "data" / "MANIFEST.sha256"
    if cand.exists():
        data_hash = hashlib.sha256(cand.read_bytes()).hexdigest()[:16]
    ident = {"code": code_fp, "contract": contract_hash, "data_manifest": data_hash}
    expected = hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12]
    assert compute_epoch(ROOT / "contracts/tinyts_real.toml") == expected


def test_series_run_refuses_epoch_mismatch(tmp_path, capsys):
    """The series runner must exit 5 on epoch mismatch."""
    contract = _contract(tmp_path)
    from antslab.series import compute_epoch
    real_epoch = compute_epoch(str(contract))

    # write a series file with a WRONG epoch
    series_file = tmp_path / "test.toml"
    series_file.write_text(
        f'name="test"\nquestion="q"\narms=["random"]\nseeds="1"\n'
        f'max_cpu_s=100\nworkers=1\nepoch="000000000000"\n'
        f'contract="{contract}"\nbase="{tmp_path / "base"}"\n'
    )
    rc = cli.main(["series", "run", "test", "--file", str(series_file)])
    assert rc == 5
    err = capsys.readouterr().err
    assert "EPOCH MISMATCH" in err
    assert "000000000000" in err


def test_series_run_accepts_matching_epoch(tmp_path, capsys):
    """With a matching epoch the runner should delegate to seeds (dry-run)."""
    contract = _contract(tmp_path)
    from antslab.series import compute_epoch
    epoch = compute_epoch(str(contract))

    series_file = tmp_path / "test.toml"
    series_file.write_text(
        f'name="test"\nquestion="q"\narms=["random"]\nseeds="1"\n'
        f'max_cpu_s=100\nworkers=1\nepoch="{epoch}"\n'
        f'contract="{contract}"\nbase="{tmp_path / "base"}"\n'
    )
    rc = cli.main(["series", "run", "test", "--file", str(series_file), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "random_s1" in out


# ===================== 3. SERIES STATUS AND CHECK =====================

def test_series_status_and_check(tmp_path, capsys):
    """status and check work on a series with a ledger."""
    contract = _contract(tmp_path)
    from antslab.series import compute_epoch
    epoch = compute_epoch(str(contract))

    base = tmp_path / "base"
    series_file = tmp_path / "test.toml"
    series_file.write_text(
        f'name="test"\nquestion="q"\narms=["random"]\nseeds="1"\n'
        f'max_cpu_s=100\nworkers=1\nepoch="{epoch}"\n'
        f'contract="{contract}"\nbase="{base}"\n'
    )

    # run a few evals to create a ledger
    env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "seeds",
                        "--arm", "random", "--seeds", "1", "--base", str(base),
                        "--contract", str(contract), "--max-evals", "4", "--workers", "1"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr[-300:]

    # status
    capsys.readouterr()
    rc = cli.main(["series", "status", "test", "--file", str(series_file), "--base", str(base)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "random" in out

    # check
    capsys.readouterr()
    rc = cli.main(["series", "check", "test", "--file", str(series_file), "--base", str(base)])
    # should pass (epoch matches, chain ok)
    assert rc == 0


def test_series_check_detects_epoch_mismatch(tmp_path, capsys):
    """check exits non-zero when a ledger has a different epoch than the series expects."""
    contract = _contract(tmp_path)
    base = tmp_path / "base"
    wd = base / "random_s1"
    wd.mkdir(parents=True)

    # create a ledger with epoch "ffffffffffff"
    led = Ledger(wd / "ledger.sqlite")
    led.register_epoch("ffffffffffff", {"code": "x", "contract": "x", "data_manifest": "x"})
    led.close()

    series_file = tmp_path / "test.toml"
    series_file.write_text(
        f'name="test"\nquestion="q"\narms=["random"]\nseeds="1"\n'
        f'max_cpu_s=100\nworkers=1\nepoch="aabbccddeeff"\n'
        f'contract="{contract}"\nbase="{base}"\n'
    )

    capsys.readouterr()
    rc = cli.main(["series", "check", "test", "--file", str(series_file), "--base", str(base)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL" in err


# ===================== 4. BUNDLE MAKE AND VERIFY =====================

def test_bundle_make_and_verify(tmp_path):
    """Make a bundle from a toy ledger and verify it."""
    # create a small ledger
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(5):
        led.event("note", {"i": i})
    led.close()

    out = tmp_path / "bundles"
    rc = cli.main(["bundle", "make", "--name", "test_pilot", "--out", str(out), str(wd)])
    assert rc == 0

    # verify the directory
    bundle_dir = out / "test_pilot"
    assert bundle_dir.exists()
    assert (bundle_dir / "MANIFEST.json").exists()
    assert (bundle_dir / "checksums.sha256").exists()
    assert (bundle_dir / "SEAL.md").exists()
    assert (bundle_dir / "ledgers" / "wd.sqlite.gz").exists()

    manifest = json.loads((bundle_dir / "MANIFEST.json").read_text())
    assert manifest["bundle_format"] == 1
    assert "wd" in manifest["ledgers"]
    assert manifest["ledgers"]["wd"]["chain_ok"] is True

    # verify passes
    rc = cli.main(["bundle", "verify", str(bundle_dir)])
    assert rc == 0

    # verify from tar.gz
    tar = out / "test_pilot.tar.gz"
    assert tar.exists()
    sha_file = out / "test_pilot.tar.gz.sha256"
    assert sha_file.exists()
    rc = cli.main(["bundle", "verify", str(tar)])
    assert rc == 0


def test_bundle_verify_detects_tampered_gz(tmp_path):
    """Flipping one byte in a gz must make verify fail."""
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(5):
        led.event("note", {"i": i})
    led.close()

    out = tmp_path / "bundles"
    cli.main(["bundle", "make", "--name", "tamper", "--out", str(out), str(wd)])

    # tamper: flip one byte in the gz
    gz = out / "tamper" / "ledgers" / "wd.sqlite.gz"
    data = bytearray(gz.read_bytes())
    data[len(data) // 2] ^= 0xFF
    gz.write_bytes(bytes(data))

    rc = cli.main(["bundle", "verify", str(out / "tamper")])
    assert rc == 1


def test_bundle_verify_detects_edited_ledger(tmp_path):
    """A row edited in a ledger copy must make verify fail via chain check."""
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(5):
        led.event("note", {"i": i})
    led.close()

    out = tmp_path / "bundles"
    cli.main(["bundle", "make", "--name", "edit_test", "--out", str(out), str(wd)])

    # decompress, edit a row, recompress -- but checksums.sha256 is stale
    gz = out / "edit_test" / "ledgers" / "wd.sqlite.gz"
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "ledger.sqlite"
        with gzip.open(gz, "rb") as fi, open(raw, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        # edit the payload of an event
        import sqlite3
        conn = sqlite3.connect(raw)
        conn.execute("UPDATE events SET payload='tampered' WHERE seq=3")
        conn.commit()
        conn.close()
        # recompress
        with open(raw, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)

    rc = cli.main(["bundle", "verify", str(out / "edit_test")])
    assert rc == 1


def test_bundle_verify_detects_deleted_event(tmp_path):
    """Deleting an event from a ledger copy must make verify fail."""
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(5):
        led.event("note", {"i": i})
    led.close()

    out = tmp_path / "bundles"
    cli.main(["bundle", "make", "--name", "del_test", "--out", str(out), str(wd)])

    # decompress, delete an event, recompress
    gz = out / "del_test" / "ledgers" / "wd.sqlite.gz"
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "ledger.sqlite"
        with gzip.open(gz, "rb") as fi, open(raw, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        import sqlite3
        conn = sqlite3.connect(raw)
        conn.execute("DELETE FROM events WHERE seq=3")
        conn.commit()
        conn.close()
        with open(raw, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)

    rc = cli.main(["bundle", "verify", str(out / "del_test")])
    assert rc == 1


def test_bundle_verify_detects_manifest_change(tmp_path):
    """Editing MANIFEST.json must make verify fail (checksums mismatch)."""
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(3):
        led.event("note", {"i": i})
    led.close()

    out = tmp_path / "bundles"
    cli.main(["bundle", "make", "--name", "mf_test", "--out", str(out), str(wd)])

    # tamper with MANIFEST.json
    mf = out / "mf_test" / "MANIFEST.json"
    data = json.loads(mf.read_text())
    data["name"] = "TAMPERED"
    mf.write_text(json.dumps(data))

    rc = cli.main(["bundle", "verify", str(out / "mf_test")])
    assert rc == 1


def test_bundle_with_note(tmp_path):
    """--note includes the note text in SEAL.md."""
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    led.event("note", {"i": 0})
    led.close()

    note = tmp_path / "note.md"
    note.write_text("This is the pilot run note.\n")

    out = tmp_path / "bundles"
    rc = cli.main(["bundle", "make", "--name", "noted", "--out", str(out), "--note", str(note), str(wd)])
    assert rc == 0
    seal = (out / "noted" / "SEAL.md").read_text()
    assert "pilot run note" in seal
    assert "private until scrubbed" in seal.lower()


def test_bundle_readonly_snapshot(tmp_path):
    """make must not modify the source ledger."""
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(3):
        led.event("note", {"i": i})
    head_before = led.head()
    led.close()

    import shutil
    stat_before = (wd / "ledger.sqlite").stat()

    out = tmp_path / "bundles"
    cli.main(["bundle", "make", "--name", "ro_test", "--out", str(out), str(wd)])

    # source untouched
    led2 = Ledger.open_readonly(wd / "ledger.sqlite")
    assert led2.head() == head_before
    led2.close()


def test_bundle_multiple_workdirs(tmp_path):
    """make accepts multiple workdirs."""
    wds = []
    for name in ("arm_a", "arm_b"):
        wd = tmp_path / name
        led = Ledger(wd / "ledger.sqlite")
        for i in range(3):
            led.event("note", {"arm": name, "i": i})
        led.close()
        wds.append(str(wd))

    out = tmp_path / "bundles"
    rc = cli.main(["bundle", "make", "--name", "multi", "--out", str(out)] + wds)
    assert rc == 0

    manifest = json.loads((out / "multi" / "MANIFEST.json").read_text())
    assert "arm_a" in manifest["ledgers"]
    assert "arm_b" in manifest["ledgers"]

    rc = cli.main(["bundle", "verify", str(out / "multi")])
    assert rc == 0


# ===================== 5. DEPLOY: SYSTEMD AND SCRIPTS =====================

def test_series_service_unit_exists_and_prevents_exit_5():
    text = (ROOT / "deploy/ants-series@.service").read_text()
    assert "RestartPreventExitStatus=3 4 5 6" in text
    assert "OOMScoreAdjust=800" in text            # above the arms' 500: under memory pressure the series dies first
    assert "KillMode=mixed" in text
    assert "TimeoutStopSec=90" in text
    assert "CPUQuota=180%" in text
    assert "MemoryMax=1500M" in text
    assert "OMP_NUM_THREADS=1" in text
    assert "VECLIB_MAXIMUM_THREADS=1" in text


def test_series_backup_unit_and_timer_exist():
    assert (ROOT / "deploy/ants-series-backup@.service").exists()
    assert (ROOT / "deploy/ants-series-backup@.timer").exists()
    text = (ROOT / "deploy/ants-series-backup@.service").read_text()
    assert "series_backup.sh" in text
    assert "Type=oneshot" in text


def test_series_backup_script_syntax():
    r = subprocess.run(["bash", "-n", str(ROOT / "deploy/series_backup.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_series_backup_script_validates_name():
    text = (ROOT / "deploy/series_backup.sh").read_text()
    assert "set -euo pipefail" in text
    assert re.search(r'\[\[.*\$NAME.*\^\[a-z0-9_\]', text)


def test_install_sh_installs_series_units():
    text = (ROOT / "deploy/install.sh").read_text()
    assert "ants-series@.service" in text
    assert "ants-series-backup@.service" in text
    assert "ants-series-backup@.timer" in text
    assert "deploy/series" in text  # copies series TOML files


def test_remote_deploy_has_series_commands():
    text = (ROOT / "deploy/remote_deploy.sh").read_text()
    assert "series-start" in text
    assert "series-stop" in text
    assert "series-status" in text


def test_remote_deploy_series_commands_validate_name(tmp_path):
    """series-start/stop/status validate the series name."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "ssh_args.log"
    (fake_bin / "ssh").write_text("#!/bin/sh\necho \"$@\" >> \"$FAKE_LOG\"\nexit 0\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
           "ANTS_HOST": "x", "ANTS_USER": "x", "ANTS_KEY": "/dev/null",
           "FAKE_LOG": str(log)}

    for cmd in ("series-start", "series-stop", "series-status"):
        # valid name
        r = subprocess.run(
            ["bash", str(ROOT / "deploy/remote_deploy.sh"), cmd, "stage_a"],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=10)
        assert r.returncode == 0, f"{cmd} failed: {r.stderr}"

        # bad name
        r = subprocess.run(
            ["bash", str(ROOT / "deploy/remote_deploy.sh"), cmd, "../bad"],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=10)
        assert r.returncode != 0, f"{cmd} should reject bad name"


def test_series_fixture_toml_exists():
    assert (ROOT / "tests/fixtures/series_stage_a.toml").exists()


def test_shell_scripts_syntax():
    """All shell scripts pass bash -n."""
    for s in (ROOT / "deploy/series_backup.sh", ROOT / "deploy/install.sh", ROOT / "deploy/remote_deploy.sh"):
        r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
        assert r.returncode == 0, f"{s.name}: {r.stderr}"


# ---- review fixes (2026-10-17): the sealed snapshot is not migrated, tar extraction is safe, an archive can be re-sealed, one runner per series

def _old_schema_ledger(path):
    from antslab.ledger import Ledger
    led = Ledger(path)
    led.event("note", {"a": 1})
    led.conn.execute("PRAGMA user_version=1")
    led.close()
    return path


def test_a_bundle_seals_the_snapshot_exactly_and_does_not_migrate_it(tmp_path):
    import gzip
    import json
    import sqlite3
    from antslab import bundle
    from antslab.ledger import SCHEMA_VERSION
    wd = tmp_path / "wd" / "pilot"
    wd.mkdir(parents=True)
    _old_schema_ledger(wd / "ledger.sqlite")
    before = sqlite3.connect(f"file:{wd / 'ledger.sqlite'}?mode=ro", uri=True).execute("PRAGMA user_version").fetchone()[0]
    assert before < SCHEMA_VERSION
    bundle.make_bundle("t", tmp_path / "out", [wd])
    led = json.loads((tmp_path / "out" / "t" / "MANIFEST.json").read_text())["ledgers"]["pilot"]
    raw = gzip.decompress((tmp_path / "out" / "t" / "ledgers" / "pilot.sqlite.gz").read_bytes())
    import hashlib
    assert hashlib.sha256(raw).hexdigest() == led["raw_sha256"]                      # the gz holds exactly the snapshot whose hash is recorded
    p = tmp_path / "raw.sqlite"
    p.write_bytes(raw)
    assert sqlite3.connect(p).execute("PRAGMA user_version").fetchone()[0] == before    # and it was not migrated by looking at it
    assert led["schema_version"] == before
    assert bundle.verify_bundle(tmp_path / "out" / "t") == 0


def test_verify_refuses_a_tar_that_writes_outside_its_directory(tmp_path, capsys):
    import io
    import tarfile
    from antslab import bundle
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        info = tarfile.TarInfo("../escaped.txt")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"bad"))
        link = tarfile.TarInfo("b/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    assert bundle.verify_bundle(evil) == 1
    assert not (tmp_path.parent / "escaped.txt").exists() and "unsafe entry" in capsys.readouterr().out


def test_an_archived_read_only_workdir_can_be_sealed_again(tmp_path):
    import os
    from antslab import bundle
    wd = tmp_path / "arch" / "pilot"
    wd.mkdir(parents=True)
    _old_schema_ledger(wd / "ledger.sqlite")
    for p in (wd / "ledger.sqlite", wd):
        os.chmod(p, 0o555 if p.is_dir() else 0o444)
    try:
        if os.access(wd, os.W_OK):
            import pytest
            pytest.skip("directory permissions are not enforced here (root)")
        bundle.make_bundle("again", tmp_path / "out", [wd])
        import json
        man = json.loads((tmp_path / "out" / "again" / "MANIFEST.json").read_text())
        assert man["ledgers"]["pilot"]["events_count"] >= 1 and bundle.verify_bundle(tmp_path / "out" / "again") == 0
    finally:
        os.chmod(wd, 0o755)
        os.chmod(wd / "ledger.sqlite", 0o644)


def test_seal_md_states_what_verification_cannot_detect(tmp_path):
    from antslab import bundle
    wd = tmp_path / "wd" / "x"
    wd.mkdir(parents=True)
    _old_schema_ledger(wd / "ledger.sqlite")
    bundle.make_bundle("s", tmp_path / "out", [wd])
    text = (tmp_path / "out" / "s" / "SEAL.md").read_text()
    assert "does **not** detect a deliberate, self-consistent rewrite" in text and "off this machine" in text


def test_a_series_file_with_a_base_outside_the_rules_is_refused(tmp_path):
    from antslab import series
    f = tmp_path / "s.toml"
    base = open(Path(__file__).resolve().parents[1] / "deploy" / "series" / "e43a.toml").read()
    f.write_text(base + '\nbase = "/tmp/../etc/x"\n')
    import pytest
    with pytest.raises(series.SeriesError, match="absolute path without"):
        series.load_series(f)
    f.write_text(base + '\nbase = "relative/dir"\n')
    with pytest.raises(series.SeriesError):
        series.load_series(f)


def test_a_second_runner_on_the_same_series_directory_exits_3(tmp_path):
    import fcntl
    from antslab import series
    contract = Path(__file__).resolve().parents[1] / "contracts" / "tinyts_real.toml"
    f = tmp_path / "locktest.toml"
    f.write_text(f'name = "locktest"\nquestion = "q"\narms = ["portfolio"]\nseeds = "1"\nmax_cpu_s = 10\nworkers = 1\n'
                 f'epoch = "{series.compute_epoch(str(contract))}"\ncontract = "{contract}"\nbase = "{tmp_path / "base"}"\n')
    (tmp_path / "base").mkdir()
    held = open(tmp_path / "base" / ".series.lock", "a+")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    ns = type("A", (), {"name": "locktest", "file": str(f), "base": None, "dry_run": True, "llm_env": None})()
    try:
        assert series.run_series(ns) == 3
    finally:
        held.close()
    assert series.run_series(ns) == 0                                       # released: a dry run goes through


def test_seeds_refuse_model_arms_and_live_arms(tmp_path, monkeypatch, capsys):
    from antslab import seeds
    ns = lambda **k: type("A", (), {"arm": "portfolio", "seeds": "1", "base": str(tmp_path), "contract": None, "max_evals": 2, "max_cpu_s": None,
                                    "workers": 1, "dry_run": False, "llm_env": None, **k})()
    assert seeds.run_seeds(ns(arm="portfolio_llm")) == 1                                    # the cap is per ledger
    assert "daily cap is per ledger" in capsys.readouterr().err
    monkeypatch.setattr(seeds, "active_arm_units", lambda: ["ants@portfolio.service"])
    contract = str(Path(__file__).resolve().parents[1] / "contracts/tinyts_real.toml")
    assert seeds.run_seeds(ns(contract=contract)) == 6                                      # 6 = a person must stop the arms first
    assert "ants@portfolio.service" in capsys.readouterr().err


def test_bundle_refuses_overwrite_bad_names_and_uniquifies_same_basenames(tmp_path):
    import json
    import stat
    from antslab import bundle
    a, b = tmp_path / "x" / "pilot", tmp_path / "y" / "pilot"
    for w in (a, b):
        w.mkdir(parents=True)
        _old_schema_ledger(w / "ledger.sqlite")
    out = tmp_path / "out"
    bundle.make_bundle("one", out, [a, b])
    names = sorted(json.loads((out / "one" / "MANIFEST.json").read_text())["ledgers"])
    assert len(names) == 2 and names[0] != names[1]                                   # neither overwrote the other
    assert bundle.verify_bundle(out / "one") == 0
    with pytest.raises(bundle.BundleError):
        bundle.make_bundle("one", out, [a])                                           # a sealed bundle is never rewritten
    for bad in ("../x", "a/b", "", ".hidden", "x" * 80):
        with pytest.raises(bundle.BundleError):
            bundle.make_bundle(bad, out, [a])
    assert not (tmp_path / "x.tar.gz").exists()
    assert stat.S_IMODE((out / "one" / "SEAL.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((out / "one.tar.gz").stat().st_mode) == 0o600


def test_a_failed_make_leaves_nothing_that_blocks_the_retry(tmp_path):
    from antslab import bundle
    out = tmp_path / "out"
    with pytest.raises(bundle.BundleError):
        bundle.make_bundle("t", out, [tmp_path / "no_such_workdir"])
    assert not (out / "t").exists()
    wd = tmp_path / "wd" / "p"
    wd.mkdir(parents=True)
    _old_schema_ledger(wd / "ledger.sqlite")
    bundle.make_bundle("t", out, [wd])


def test_immutable_fallback_is_refused_when_a_write_ahead_log_is_unmerged(tmp_path, monkeypatch):
    import sqlite3
    from antslab import bundle
    wd = tmp_path / "wd" / "p"
    wd.mkdir(parents=True)
    _old_schema_ledger(wd / "ledger.sqlite")
    (wd / "ledger.sqlite-wal").write_bytes(b"x" * 64)
    real = sqlite3.connect

    def fake(target, *a, **k):
        if isinstance(target, str) and "mode=ro" in target and "immutable=1" not in target:
            raise sqlite3.OperationalError("unable to open database file")
        return real(target, *a, **k)
    monkeypatch.setattr(bundle.sqlite3, "connect", fake)
    with pytest.raises(bundle.BundleError, match="write-ahead log"):
        bundle._snapshot_readonly(wd / "ledger.sqlite", tmp_path / "snap.sqlite")


def test_make_refuses_a_workdir_name_verify_would_reject_before_writing_anything(tmp_path):
    from antslab import bundle
    out = tmp_path / "out"
    for bad in ("my run", ".hidden", "naïve", "x" * 70):
        wd = tmp_path / "wd" / bad
        wd.mkdir(parents=True)
        _old_schema_ledger(wd / "ledger.sqlite")
        with pytest.raises(bundle.BundleError, match="plain names"):
            bundle.make_bundle("b", out, [wd])
        assert not out.exists() or not list(out.iterdir())
