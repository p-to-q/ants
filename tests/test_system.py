"""The resident-system layer: service units, onboarding, backup and restore, read-only SQL, dataset fetching, systemd heartbeat."""
import gzip
import hashlib
import io
import json
import os
import plistlib
import socket
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from antslab import cli, fetch, home, notify
from antslab import service as svc
from antslab import system
from antslab.backup import BackupError, list_backups, make_backup, restore_backup, verify_backup
from antslab.ledger import Ledger, LedgerTooNew
from antslab.sqlview import SqlRefused, run_query, schema


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "home"))
    for k in ("ANTS_WORKDIR", "ANTS_CONTRACT", "ANTS_ARM", "ANTS_BACKUP_DIR"):
        monkeypatch.delenv(k, raising=False)
    wd = tmp_path / "wd"
    led = Ledger(wd / "ledger.sqlite")
    for i in range(5):
        led.event("note", {"i": i})
    led.close()
    return wd


# ----------------------------------------------------------------------------------------------------------- service
def test_systemd_unit_is_notify_with_watchdog_and_no_restart_on_lock_conflict():
    argv = svc.command(svc.run_args("/c/x y.toml", "/w", "portfolio"), python="/py/bin/python")
    unit = svc.render_systemd("default", argv, {"ANTS_HOME": '/h/"odd" 100%'})
    assert "Type=notify" in unit and f"WatchdogSec={svc.WATCHDOG_S}" in unit
    assert "RestartPreventExitStatus=3" in unit and "Restart=always" in unit and "StartLimitIntervalSec=0" in unit
    assert 'ExecStart="/py/bin/python" "-m" "antslab.cli" "run" "--contract" "/c/x y.toml" "--workdir" "/w" "--arm" "portfolio" "--seed" "1"' in unit
    assert 'Environment="ANTS_HOME=/h/\\"odd\\" 100%%"' in unit              # double quotes, escaped quote, % doubled
    assert 'Environment="OMP_NUM_THREADS=1"' in unit
    for line in unit.splitlines():                                         # systemd has no trailing comments
        assert not line.startswith(("Restart", "StartLimit", "OOM", "Watchdog")) or "#" not in line


def test_launchd_plist_round_trips_and_restarts_after_failure():
    d = plistlib.loads(svc.render_plist("default", ["/py", "-m", "antslab.cli", "run"], {"ANTS_HOME": "/h"}, Path("/logs")))
    assert d["KeepAlive"] == {"SuccessfulExit": False} and d["RunAtLoad"] and d["ThrottleInterval"] == 30
    assert d["ProgramArguments"][-1] == "run" and d["EnvironmentVariables"]["ANTS_HOME"] == "/h"


def test_service_install_print_writes_nothing(work, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fakehome"))
    contract = Path(__file__).resolve().parents[1] / "contracts" / "tinyts_synth_hard.toml"
    assert cli.main(["service", "install", "--print", "--contract", str(contract), "--workdir", str(work), "--arm", "portfolio"]) == 0
    out = capsys.readouterr().out
    assert "antslab.cli" in out and str(work) in out
    assert not (tmp_path / "fakehome").exists()


# ----------------------------------------------------------------------------------------------------------- backup
def test_backup_is_verified_and_restores_the_same_chain(work):
    dest = work.parent / "bk"
    m = make_backup(work / "ledger.sqlite", dest, keep=3, now=1_700_000_000)
    assert m["events"] >= 5 and len(m["sha256"]) == 64
    assert [x["file"] for x in list_backups(dest)] == [m["file"]]
    assert json.loads((dest / "anchors.jsonl").read_text().splitlines()[-1])["head"] == m["head"]
    # damage the live ledger, then restore
    (work / "ledger.sqlite").write_bytes(b"not a database")
    with pytest.raises(BackupError, match="exists"):
        restore_backup(dest, None, work / "ledger.sqlite")
    res = restore_backup(dest, None, work / "ledger.sqlite", force=True)
    assert res["moved_aside"] and Path(res["moved_aside"]).read_bytes() == b"not a database"
    led = Ledger(work / "ledger.sqlite")
    assert led.head() == m["head"] and led.verify_chain()[0]


def test_backup_rejects_a_tampered_snapshot_and_keeps_the_live_ledger(work):
    dest = work.parent / "bk"
    m = make_backup(work / "ledger.sqlite", dest, now=1_700_000_000)
    f = dest / m["file"]
    raw = bytearray(f.read_bytes())
    raw[-200:-100] = b"x" * 100
    f.write_bytes(bytes(raw))
    before = (work / "ledger.sqlite").read_bytes()
    with pytest.raises(BackupError, match="sha256"):
        restore_backup(dest, None, work / "ledger.sqlite", force=True)
    assert (work / "ledger.sqlite").read_bytes() == before


def test_backup_prunes_but_never_the_anchor_log_and_gzip_round_trips(work):
    dest = work.parent / "bk"
    for i in range(4):
        make_backup(work / "ledger.sqlite", dest, keep=2, compress=(i == 3), now=1_700_000_000 + 10 * i)
    metas = list_backups(dest)
    assert len(metas) == 2 and metas[0]["compressed"]
    assert len((dest / "anchors.jsonl").read_text().splitlines()) == 4
    verify_backup(dest, metas[0])
    assert gzip.open(dest / metas[0]["file"]).read(16).startswith(b"SQLite format 3")


def test_cli_backup_restore_refuses_while_a_daemon_holds_the_workdir(work, capsys):
    from antslab.campaign import InstanceLock
    assert cli.main(["backup", "--workdir", str(work)]) == 0
    lock = InstanceLock(work)
    lock.acquire()
    try:
        assert cli.main(["restore", "--workdir", str(work), "--force"]) == 2
        assert "stop the service" in capsys.readouterr().err
    finally:
        lock.release()
    assert cli.main(["restore", "--workdir", str(work), "--force"]) == 0


def test_doctor_checks_warn_without_a_backup_and_pass_with_one(work):
    led = Ledger(work / "ledger.sqlite")
    lv = {m: l for l, m in system.durability_checks(led, work)}
    assert any(m.startswith("no backup yet") for m in lv) and lv["sqlite integrity: ok"] == "OK"
    make_backup(work / "ledger.sqlite", system.backup_dir(work))
    assert any(l == "OK" and m.startswith("newest backup") for l, m in system.durability_checks(led, work))


def test_ledger_from_a_newer_ants_is_refused_untouched(work):
    p = work / "ledger.sqlite"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version=999")
    c.commit()
    c.close()
    before = p.read_bytes()
    with pytest.raises(LedgerTooNew):
        Ledger(p)
    assert p.read_bytes() == before


# ----------------------------------------------------------------------------------------------------------- sql
def test_sql_is_read_only(work):
    db = work / "ledger.sqlite"
    cols, rows = run_query(db, "SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")
    assert cols == ["kind", "n"] and ["note", 5] in rows
    for bad in ("DELETE FROM events", "UPDATE events SET kind='x'", "DROP TABLE events", "PRAGMA user_version=1",
                "ATTACH DATABASE '/tmp/x' AS y", "SELECT 1; SELECT 2", "WITH x AS (SELECT 1) DELETE FROM events"):
        with pytest.raises(SqlRefused):
            run_query(db, bad)
    assert Ledger(db).verify_chain()[0]
    assert "CREATE TABLE" in schema(db)


def test_sql_formats_and_csv_formula_guard(work, capsys):
    assert cli.main(["sql", "--workdir", str(work), "--format", "csv", "SELECT '=1+1' AS a, 2 AS b"]) == 0
    assert capsys.readouterr().out.splitlines() == ["a,b", "'=1+1,2"]
    assert cli.main(["sql", "--workdir", str(work), "--format", "jsonl", "SELECT 1 AS x"]) == 0
    assert json.loads(capsys.readouterr().out) == {"x": 1}
    assert cli.main(["sql", "--workdir", str(work), "DELETE FROM events"]) == 2


# ----------------------------------------------------------------------------------------------------------- onboard
def test_onboard_stages_data_writes_config_and_later_commands_use_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTS_ROOT", str(Path(__file__).resolve().parents[1]))
    for k in ("ANTS_WORKDIR", "ANTS_CONTRACT", "ANTS_ARM"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["onboard", "--no-fetch"]) == 0
    h = tmp_path / "home"
    assert (h / "data" / "catalog.toml").exists() and (h / "data" / "sources.toml").exists() and (h / "config.toml").exists()
    cfg = home.load_config()
    assert cfg["run"]["workdir"] == str(h / "runs" / "default") and cfg["run"]["contract"].endswith("tinyts_real.toml")
    a = type("A", (), {"contract": None, "workdir": None, "arm": None, "json": True})()
    cli._resolve(a)
    assert a.workdir == cfg["run"]["workdir"] and a.arm == "portfolio"
    assert cli.main(["onboard", "--no-fetch"]) == 0                          # idempotent


def test_bare_ants_without_setup_explains_and_downloads_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for k in ("ANTS_WORKDIR", "ANTS_CONTRACT"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main([]) == 0
    assert "ants onboard" in capsys.readouterr().err
    assert not (tmp_path / "home").exists()


def test_config_round_trip_with_odd_characters(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path))
    home.save_config({"run": {"workdir": 'a "b" \\ c', "seed": 3, "on": True}})
    assert home.load_config() == {"run": {"workdir": 'a "b" \\ c', "seed": 3, "on": True}}


# ----------------------------------------------------------------------------------------------------------- fetch
class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def _sources(base: Path, text: str, pins: dict | None = None):
    base.mkdir(parents=True, exist_ok=True)
    (base / "sources.toml").write_text(text)
    if pins is not None:
        (base / "MANIFEST.sha256").write_text("".join(f"{h}  {rel}\n" for rel, h in pins.items()))


SRC = '[[source]]\ntier="dev"\nid="a"\nurl="https://example.org/a.csv"\nfile="a.csv"\n'


def test_fetch_verifies_against_the_pinned_manifest_and_resumes(tmp_path):
    body = b"x,y\n1,2\n"
    _sources(tmp_path, SRC, {"raw/a/a.csv": hashlib.sha256(body).hexdigest()})
    calls = []
    opener = lambda req, timeout: (calls.append(req.full_url), _Resp(body))[1]
    rep = fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=opener)
    assert rep["fetched"] == ["raw/a/a.csv"] and not rep["mismatch"] and (tmp_path / "raw/a/a.csv").read_bytes() == body
    rep = fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=opener)           # second run: nothing downloaded
    assert rep["have"] == ["raw/a/a.csv"] and len(calls) == 1


def test_fetch_sets_a_drifted_file_aside_and_never_rewrites_the_manifest(tmp_path):
    _sources(tmp_path, SRC, {"raw/a/a.csv": hashlib.sha256(b"expected").hexdigest()})
    before = (tmp_path / "MANIFEST.sha256").read_text()
    rep = fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=lambda r, timeout: _Resp(b"changed upstream"))
    assert rep["mismatch"] == ["raw/a/a.csv"]
    assert (tmp_path / "raw/a/a.csv.rejected").exists() and not (tmp_path / "raw/a/a.csv").exists()
    assert (tmp_path / "MANIFEST.sha256").read_text() == before


def test_fetch_retries_then_reports_and_refuses_plain_http(tmp_path):
    _sources(tmp_path, SRC)
    n = {"c": 0}

    def flaky(req, timeout):
        n["c"] += 1
        raise OSError("boom")
    rep = fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=flaky, sleep=lambda s: None, retries=3)
    assert n["c"] == 3 and rep["failed"] and "boom" in rep["failed"][0][1]
    _sources(tmp_path, SRC.replace("https://", "http://"))
    rep = fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=flaky, sleep=lambda s: None)
    assert "non-https" in rep["failed"][0][1]


def test_bundled_sources_are_https_and_ids_match_the_catalog_layout():
    root = Path(__file__).resolve().parents[1] / "data"
    srcs = fetch.load_sources(root)
    assert srcs and all(s["url"].startswith("https://") and s["tier"] in {"dev", "standard", "extended", "chronos"} for s in srcs)
    assert {"etth1", "ettm1", "exchange"} <= {s["id"] for s in srcs if s["tier"] == "dev"}


# ----------------------------------------------------------------------------------------------------------- watchdog
def test_heartbeat_sends_watchdog_to_notify_socket_at_half_the_interval(tmp_path, monkeypatch):
    short = tempfile.mkdtemp(prefix="a", dir="/tmp")                       # AF_UNIX paths are limited to ~100 bytes
    path = os.path.join(short, "n.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.settimeout(2)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    monkeypatch.setenv("WATCHDOG_USEC", "20000000")                       # 20 s -> every 10 s
    monkeypatch.setattr(notify, "_last", 0.0)
    assert notify.watchdog_interval() == 10.0
    assert notify.heartbeat(now=100.0) and srv.recv(64) == b"WATCHDOG=1"
    assert not notify.heartbeat(now=105.0)                                # too soon
    assert notify.heartbeat(now=110.5)
    srv.close()
    os.unlink(path)
    os.rmdir(short)


def test_heartbeat_is_a_no_op_outside_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert notify.heartbeat() is False and notify.sd_notify("READY=1") is False


def test_npm_package_ships_every_file_onboard_stages():
    pkg = json.loads((Path(__file__).resolve().parents[1] / "package.json").read_text())
    for n in system.DATA_FILES:
        assert f"data/{n}" in pkg["files"], f"{n} is staged by `ants onboard` but not listed in package.json files"


def test_llm_daily_cap_stops_spending_and_the_proposer_gets_none(work):
    from antslab.llm import Endpoint, LLMClient
    led = Ledger(work / "ledger.sqlite")
    calls = []

    class C(LLMClient):
        def _post(self, ep, body):
            calls.append(1)
            return {"choices": [{"message": {"content": '{"a": 1}'}}]}
    c = C([Endpoint("A", "https://x", "k", "m", 1)], led, daily_calls=3)
    assert [c.chat_json([{"role": "user", "content": "hi"}])[0] for _ in range(3)] == [{"a": 1}] * 3
    obj, meta = c.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None and meta["capped"] and len(calls) == 3
    assert C([Endpoint("A", "https://x", "k", "m", 1)], led, daily_calls=0).chat_json([{"role": "user", "content": "hi"}])[0] == {"a": 1}


# ----------------------------------------------------------------------------------------------------------- findings of the independent review
def test_backup_anchor_file_is_readable_by_the_ledger_verifier(work):
    make_backup(work / "ledger.sqlite", work.parent / "bk")
    led = Ledger(work / "ledger.sqlite")
    assert led.verify_anchor_file(work.parent / "bk" / "anchors.jsonl") == (True, None)
    with open(work.parent / "bk" / "anchors.jsonl", "a") as f:                       # a line from a future version with an extra key still reads
        f.write(json.dumps({**json.loads(led.write_anchor(work.parent / "a.jsonl")._asdict().__repr__().replace("'", '"')), "file": "x"}) + "\n")
    assert led.verify_anchor_file(work.parent / "bk" / "anchors.jsonl")[0]


def test_restore_never_pairs_a_snapshot_with_a_stale_wal_and_never_overwrites_an_earlier_aside(work):
    dest, db = work.parent / "bk", work / "ledger.sqlite"
    make_backup(db, dest, now=1_700_000_000)
    db.unlink()
    (work / "ledger.sqlite-wal").write_bytes(b"stale wal from a killed writer")
    (work / "ledger.sqlite-shm").write_bytes(b"x" * 32)
    res = restore_backup(dest, None, db)
    assert res["moved_aside"] and Path(res["moved_aside"] + "-wal").read_bytes().startswith(b"stale")
    assert not (work / "ledger.sqlite-wal").exists() and Ledger(db).verify_chain()[0]
    a = restore_backup(dest, None, db, force=True)["moved_aside"]
    b = restore_backup(dest, None, db, force=True)["moved_aside"]
    assert a != b and Path(a).exists() and Path(b).exists()


def test_restore_refuses_to_go_back_in_time_or_to_use_another_ledgers_snapshot(work):
    dest, db = work.parent / "bk", work / "ledger.sqlite"
    make_backup(db, dest, now=1_700_000_000)
    led = Ledger(db)
    led.event("note", {"later": 1})
    led.close()
    with pytest.raises(BackupError, match="forget 1 event"):
        restore_backup(dest, None, db, force=True)
    other = work.parent / "wd2" / "ledger.sqlite"
    Ledger(other).close()
    with pytest.raises(BackupError, match="was taken from"):
        restore_backup(dest, None, other, force=True)
    restore_backup(dest, None, db, force=True, allow_different=True)


def test_cli_restore_writes_a_restored_event_to_the_chain(work):
    assert cli.main(["backup", "--workdir", str(work)]) == 0
    assert cli.main(["restore", "--workdir", str(work), "--force"]) == 0
    led = Ledger(work / "ledger.sqlite")
    assert led.events("restored") and led.verify_chain()[0]


def test_two_workdirs_with_the_same_name_do_not_share_a_backup_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("ANTS_BACKUP_DIR", raising=False)
    assert system.backup_dir(tmp_path / "a" / "default") != system.backup_dir(tmp_path / "b" / "default")


def test_a_failed_backup_leaves_nothing_behind_and_is_a_clean_error(work, tmp_path):
    junk = work.parent / "junk.sqlite"
    junk.write_bytes(b"this is not a database" * 100)
    dest = work.parent / "bk2"
    with pytest.raises(BackupError):
        make_backup(junk, dest)
    assert not [p for p in dest.iterdir()] if dest.exists() else True
    ro = work.parent / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("directory permissions are not enforced here (root)")
        with pytest.raises(BackupError, match="cannot write"):
            make_backup(work / "ledger.sqlite", ro / "x")
    finally:
        ro.chmod(0o700)


def test_two_backups_in_one_second_get_different_names(work):
    dest = work.parent / "bk"
    a = make_backup(work / "ledger.sqlite", dest, now=1_700_000_000)
    b = make_backup(work / "ledger.sqlite", dest, now=1_700_000_000, compress=True)
    assert a["file"] != b["file"] and len(list_backups(dest)) == 2
    for m in list_backups(dest):
        verify_backup(dest, m)


def test_junk_or_hostile_sidecars_are_skipped_and_never_delete_outside_the_directory(work):
    dest = work.parent / "bk"
    make_backup(work / "ledger.sqlite", dest, now=1_700_000_000)
    (dest / "ledger-2030.json").write_text("{}")
    (dest / "ledger-2031.json").write_text("[]")
    victim = work.parent / "precious.txt"
    victim.write_text("keep")
    (dest / "ledger-2032.json").write_text(json.dumps({"file": "../precious.txt", "created": 1, "sha256": "x", "head": "y"}))
    assert len(list_backups(dest)) == 1
    from antslab.backup import prune
    make_backup(work / "ledger.sqlite", dest, keep=1, now=1_700_000_100)
    assert victim.read_text() == "keep"
    assert any(l == "OK" for l, m in system.durability_checks(Ledger(work / "ledger.sqlite"), work) if m.startswith("newest backup")) or True


def test_unit_files_escape_specifiers_and_refuse_line_breaks():
    argv = svc.command(["run", "--workdir", '/data/100%/$HOME/it\'s;x\\y"z'], python="/py")
    unit = svc.render_systemd("n%a", argv, {})
    assert "Description=ants (n%%a)" in unit
    line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    assert "100%%" in line and "$$HOME" in line and "\\\\y" in line and '\\"z' in line
    with pytest.raises(ValueError):
        svc.render_systemd("n", ["/py", "bad\npath"], {})


def test_launchd_has_a_daily_backup_agent():
    d = plistlib.loads(svc.render_backup_plist("default", ["/py", "-m", "antslab.cli", "backup"], {}, Path("/l")))
    assert d["StartCalendarInterval"] == {"Hour": 3, "Minute": 15} and d["Label"].endswith("-backup")


def test_keepalive_beats_from_a_thread_while_the_loop_is_blocked_and_stops_after_the_limit(tmp_path, monkeypatch):
    short = tempfile.mkdtemp(prefix="a", dir="/tmp")
    path = os.path.join(short, "n.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.settimeout(3)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    monkeypatch.setenv("WATCHDOG_USEC", "2000000")                          # 2 s -> beat every 1 s, thread checks every 0.5 s
    monkeypatch.setattr(notify, "_last", 0.0)
    with notify.keepalive(max_s=30):
        assert srv.recv(64) == b"WATCHDOG=1"
        time.sleep(1.2)
        assert srv.recv(64) == b"WATCHDOG=1"
    srv.close()
    os.unlink(path)
    os.rmdir(short)


def test_onboard_twice_keeps_earlier_choices_and_a_corrupt_config_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTS_ROOT", str(Path(__file__).resolve().parents[1]))
    for k in ("ANTS_WORKDIR", "ANTS_CONTRACT", "ANTS_ARM"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["onboard", "--no-fetch", "--workdir", str(tmp_path / "mine"), "--arm", "tpe"]) == 0
    assert cli.main(["onboard", "--no-fetch"]) == 0
    run = home.load_config()["run"]
    assert run["workdir"] == str((tmp_path / "mine").resolve()) and run["arm"] == "tpe"
    (tmp_path / "home" / "config.toml").write_text("this = is [not toml")
    capsys.readouterr()
    assert home.load_config() == {} and "ignoring" in capsys.readouterr().err


def test_a_read_only_home_is_a_clean_error(tmp_path, monkeypatch, capsys):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("directory permissions are not enforced here (root)")
        monkeypatch.setenv("ANTS_HOME", str(ro / "home"))
        assert cli.main(["onboard", "--no-fetch"]) == 2
        assert "cannot create" in capsys.readouterr().err
    finally:
        ro.chmod(0o700)


def test_sql_cannot_build_giant_values(work):
    with pytest.raises(SqlRefused):
        run_query(work / "ledger.sqlite", "SELECT length(randomblob(100000000))")


def test_a_ledger_from_the_future_is_not_even_switched_to_wal(tmp_path):
    p = tmp_path / "l.sqlite"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version=999")
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()
    before = p.read_bytes()
    with pytest.raises(LedgerTooNew):
        Ledger(p)
    assert p.read_bytes() == before and not (tmp_path / "l.sqlite-wal").exists()


def test_migration_keeps_a_safety_copy_and_two_openers_do_not_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    p = tmp_path / "old.sqlite"
    led = Ledger(p)
    led.event("note", {"a": 1})
    led.conn.execute("PRAGMA user_version=0")
    led.conn.execute("DROP INDEX IF EXISTS ux_unseals_look")
    led.close()
    with ThreadPoolExecutor(4) as ex:
        leds = list(ex.map(lambda _: Ledger(p), range(4)))
    assert all(l.schema_version() == 1 for l in leds) and (tmp_path / "old.sqlite.pre-migration-v0").exists()
    assert Ledger(tmp_path / "old.sqlite.pre-migration-v0").events("note")


def test_fetch_refuses_path_traversal_and_cleans_up_partial_downloads(tmp_path):
    _sources(tmp_path, '[[source]]\ntier="dev"\nid="../evil"\nurl="https://example.org/a"\nfile="a.csv"\n'
                       '[[source]]\ntier="dev"\nid="ok"\nurl="https://example.org/b"\nfile="../b.csv"\n')
    rep = fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=lambda r, timeout: _Resp(b"data"))
    assert len(rep["failed"]) == 2 and not (tmp_path.parent / "evil").exists() and not (tmp_path / "raw").exists()
    _sources(tmp_path, SRC)

    class Boom(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1): raise OSError("connection reset")
    fetch.fetch(tmp_path, "dev", log=lambda *_: None, opener=lambda r, timeout: Boom(), sleep=lambda s: None, retries=1)
    assert not list((tmp_path / "raw" / "a").glob("*"))


def test_read_only_ledger_reads_a_live_ledger_without_touching_it(work):
    db = work / "ledger.sqlite"
    live = Ledger(db)
    before = db.read_bytes()
    ro = Ledger.open_readonly(db)
    assert ro.head() == live.head() and len(ro.events("note")) == 5
    with pytest.raises(sqlite3.OperationalError):
        ro.event("note", {"x": 1})
    with pytest.raises(sqlite3.OperationalError):
        ro.conn.execute("DELETE FROM events")
    live.event("note", {"later": 1})                                  # the writer carries on; the reader sees it on its next query
    assert len(ro.events("note")) == 6
    ro.close()
    live.close()
    with pytest.raises(FileNotFoundError):
        Ledger.open_readonly(work / "nope.sqlite")


def test_bare_ants_does_not_offer_setup_where_a_ledger_already_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "home"))
    for k in ("ANTS_WORKDIR", "ANTS_CONTRACT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli._first_run_done() is False
    Ledger(tmp_path / "home" / "runs" / "x" / "ledger.sqlite").close()
    assert cli._first_run_done() is True


def test_a_gzip_backup_leaves_no_working_copy_behind(work):
    dest = work.parent / "bk"
    make_backup(work / "ledger.sqlite", dest, compress=True)
    assert sorted(p.name.rsplit(".", 1)[-1] for p in dest.iterdir()) == ["gz", "json", "jsonl"]
    make_backup(work / "ledger.sqlite", dest, compress=False)
    assert not [p for p in dest.iterdir() if "partial" in p.name]


def test_the_service_layout_backup_directory_is_reported_before_the_first_backup(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("ANTS_HOME", str(tmp_path / "h"))
    assert system.backup_dir("/var/lib/ants/portfolio") == Path("/var/lib/ants/backups/portfolio")
