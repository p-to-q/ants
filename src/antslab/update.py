"""Knowing when a newer ants exists, and installing it on request. Standard library only.

Checking is cheap and never gets in the way: the answer is cached for a day in <home>/update.json, the lookup has a short timeout,
any failure is silent, and nothing here runs inside `ants run`. Installing is always explicit (`ants update --apply`): a resident
daemon must not change under itself. The ledger is forward-only versioned, so a newer ants can open an older ledger, and an older
ants refuses a newer one instead of damaging it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .home import home, load_config, package_root

SOURCES = {
    "npm": "https://registry.npmjs.org/ants-lab/latest",
    "github": "https://api.github.com/repos/p-to-q/ants/releases/latest",
}
MAX_AGE_S = 86400
USER_AGENT = "ants-lab (+https://github.com/p-to-q/ants)"


def current_version() -> str:
    try:
        from importlib.metadata import version
        return version("ants-lab")
    except Exception:
        return "0"


def parse_version(s: str) -> tuple:
    """'v0.2.10-rc1' -> ((0, 2, 10), 0); a release sorts above its own pre-releases; anything unparsable sorts lowest"""
    m = re.fullmatch(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})(?:[-+.]?([0-9A-Za-z.-]{1,40}))?", str(s)[:80]) if isinstance(s, str) else None
    if not m:
        return ((0,), -1)
    return (tuple(int(x) for x in m.group(1).split(".")), 0 if m.group(2) else 1)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    pad = lambda t: t + (0,) * (4 - len(t))
    return (pad(a[0]), a[1]) > (pad(b[0]), b[1])


def cache_path() -> Path:
    return home() / "update.json"


def settings() -> dict:
    u = load_config().get("update", {})
    u = u if isinstance(u, dict) else {}
    return {"check": u.get("check", True) is not False, "source": u.get("source", "npm") if u.get("source") in SOURCES else "npm"}


def fetch_latest(source: str = "npm", timeout: float = 3.0, opener=urllib.request.urlopen) -> str:
    url = SOURCES[source]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with opener(req, timeout=timeout) as r:
        d = json.loads(r.read(1 << 20).decode("utf-8", "replace"))
    v = d.get("version") if source == "npm" else d.get("tag_name")
    if not isinstance(v, str) or not parse_version(v)[0] > (0,):
        raise ValueError("the registry did not return a version")
    return v.lstrip("v")


def read_cache() -> dict:
    try:
        d = json.loads(cache_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def check(force: bool = False, timeout: float = 3.0, opener=urllib.request.urlopen, now: float | None = None) -> dict:
    """{current, latest, newer, checked, source, error}. Uses the cache unless it is older than a day (or force). Never raises."""
    t = time.time() if now is None else now
    cur, st = current_version(), settings()
    c = read_cache()
    res = {"current": cur, "latest": c.get("latest"), "checked": c.get("checked"), "source": st["source"], "error": None}
    try:
        age = t - float(c.get("checked") or 0)
    except (TypeError, ValueError):
        age = MAX_AGE_S + 1
    if force or not isinstance(c.get("latest"), str) or c.get("source") != st["source"] or age > MAX_AGE_S:
        try:
            res["latest"], res["checked"] = fetch_latest(st["source"], timeout, opener), t
            try:
                cache_path().parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_path().with_suffix(".tmp")
                tmp.write_text(json.dumps({"latest": res["latest"], "checked": t, "source": st["source"]}))
                tmp.replace(cache_path())
            except OSError:
                pass
        except Exception as e:                           # offline, rate limited, odd answer: keep what we knew
            res["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    res["newer"] = bool(res["latest"]) and is_newer(res["latest"], cur)
    return res


def install_method() -> str:
    root = package_root()
    if root and "node_modules" in root.parts:
        return "npm"
    if root and (root / ".git").exists():
        return "git"
    return "pip"


def update_command(method: str, latest: str | None = None) -> list[str] | None:
    if method == "npm":
        return ["npm", "install", "-g", f"ants-lab@{latest}" if latest else "ants-lab@latest"]
    if method == "git":
        return ["git", "-C", str(package_root()), "pull", "--ff-only"]
    return [sys.executable, "-m", "pip", "install", "--upgrade", f"ants-lab=={latest}" if latest else "ants-lab"]


def apply(res: dict, run=None) -> int:
    cmd = update_command(install_method(), res.get("latest"))
    print("running: " + " ".join(cmd))
    return (run or subprocess.run)(cmd, check=False).returncode


def notice(stream=None) -> str | None:
    """One line for interactive commands when the cache already knows a newer version; refreshes the cache in the background
    (daemon thread, short timeout) at most once a day. Silent in scripts: no terminal, ANTS_NO_UPDATE_CHECK, or `check = false`."""
    stream = stream or sys.stderr
    if os.environ.get("ANTS_NO_UPDATE_CHECK") or not settings()["check"] or not (stream.isatty() and sys.stdout.isatty()):
        return None
    c = read_cache()
    try:
        stale = time.time() - float(c.get("checked") or 0) > MAX_AGE_S
    except (TypeError, ValueError):
        stale = True
    if stale:
        threading.Thread(target=check, kwargs={"force": True}, daemon=True).start()
    cur = current_version()
    if isinstance(c.get("latest"), str) and is_newer(c["latest"], cur):
        return f"ants {c['latest']} is available (you have {cur}): run `ants update`"
    return None


def cmd_update(a) -> int:
    res = check(force=True, timeout=10.0)
    if a.json:
        print(json.dumps({**res, "install": install_method()}, indent=1))
        if not a.apply:
            return 0
    if res["error"] and not res["latest"]:
        print(f"ants update: could not reach {res['source']}: {res['error']}", file=sys.stderr)
        return 2
    if not a.json:
        print(f"installed {res['current']} · latest {res['latest']} ({res['source']}) · installed via {install_method()}")
    if not res["newer"]:
        if not a.json:
            print("up to date")
        return 0
    if not a.apply:
        if not a.json:
            print("newer version available; install it with `ants update --apply`:  " + " ".join(update_command(install_method(), res["latest"])))
        return 0
    rc = apply(res)
    if rc == 0:
        print("updated. A running service keeps the old code until it is restarted: `ants service restart`")
    return rc
