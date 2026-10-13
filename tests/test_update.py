"""Update check: version order, cache, silence in scripts, explicit install only."""
import io
import json
import time

import pytest

from antslab import update


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTS_HOME", str(tmp_path))
    monkeypatch.delenv("ANTS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "current_version", lambda: "0.2.0")
    return tmp_path


@pytest.mark.parametrize("latest,cur,newer", [("0.2.1", "0.2.0", True), ("0.10.0", "0.9.9", True), ("0.2.0", "0.2.0", False), ("0.1.9", "0.2.0", False),
                                              ("1.0.0", "1.0.0-rc1", True), ("1.0.0-rc1", "1.0.0", False), ("v0.3", "0.2.9", True), ("garbage", "0.2.0", False)])
def test_version_order(latest, cur, newer):
    assert update.is_newer(latest, cur) is newer


def _opener(version, calls):
    def op(req, timeout):
        calls.append(req.full_url)
        return Resp(json.dumps({"version": version}).encode())
    return op


def test_check_caches_for_a_day_and_survives_being_offline():
    calls = []
    r = update.check(opener=_opener("0.3.0", calls), now=1000.0)
    assert r["latest"] == "0.3.0" and r["newer"] and calls == ["https://registry.npmjs.org/ants-lab/latest"]
    update.check(opener=_opener("9.9.9", calls), now=1000.0 + 3600)               # within a day: cache, no request
    assert len(calls) == 1
    def offline(req, timeout): raise OSError("no route")
    r = update.check(opener=offline, now=1000.0 + 2 * 86400)                      # stale and offline: keep the old answer, report the error
    assert r["latest"] == "0.3.0" and r["newer"] and "OSError" in r["error"]


def test_an_odd_registry_answer_is_rejected():
    for bad in ({"version": "<script>"}, {"version": 3}, {}):
        r = update.check(force=True, opener=lambda req, timeout, b=bad: Resp(json.dumps(b).encode()))
        assert r["latest"] is None and not r["newer"] and r["error"]


def test_notice_is_silent_in_scripts_and_when_disabled(monkeypatch):
    update.cache_path().write_text(json.dumps({"latest": "0.3.0", "checked": time.time(), "source": "npm"}))
    tty = type("T", (io.StringIO,), {"isatty": lambda self: True})()
    monkeypatch.setattr("sys.stdout", tty)
    assert "0.3.0 is available" in update.notice(tty)
    assert update.notice(io.StringIO()) is None                                    # stderr is not a terminal
    monkeypatch.setenv("ANTS_NO_UPDATE_CHECK", "1")
    assert update.notice(tty) is None


def test_install_commands_per_method():
    assert update.update_command("npm", "0.3.0") == ["npm", "install", "-g", "ants-lab@0.3.0"]
    assert update.update_command("pip", "0.3.0")[-1] == "ants-lab==0.3.0"
    assert "pull" in update.update_command("git") and "--ff-only" in update.update_command("git")


def test_update_without_apply_installs_nothing(capsys, monkeypatch):
    ran = []
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: (ran.append(a), type("R", (), {"returncode": 0})())[1])
    monkeypatch.setattr(update, "install_method", lambda: "npm")
    monkeypatch.setattr(update, "fetch_latest", lambda *a, **k: "0.3.0")
    a = type("A", (), {"json": False, "apply": False})()
    assert update.cmd_update(a) == 0 and not ran and "ants update --apply" in capsys.readouterr().out
    a.apply = True
    assert update.cmd_update(a) == 0 and len(ran) == 1
