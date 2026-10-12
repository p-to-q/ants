"""Shared helpers for the failure-path ("chaos") tests. No tests live here."""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import TOML, TOY

CLI = [sys.executable, "-m", "antslab.cli"]

# a gym that finishes its evaluation, then ignores SIGTERM and lingers (a stubborn child)
STUBBORN_GYM = """
import argparse, json, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
ap = argparse.ArgumentParser()
for k in ("stage", "spec", "out", "seed", "attempt"):
    ap.add_argument("--" + k)
a = ap.parse_args()
open(a.out + "/metrics.json", "w").write(json.dumps({"val_mse": 1.0, "n_params": 10}))
time.sleep(600)
"""


def write_contract(tmp_path: Path, gym: Path | None = None, timeout: int = 20, name: str = "task.toml") -> Path:
    """The standard toy contract; with `gym` the eval command runs that script instead of antslab.gyms.toy."""
    text = TOML.format(toy=gym or TOY, timeout=timeout)
    if gym is not None:
        i = text.index("eval=[")
        j = text.index("\n", i)
        cmd = ('eval=["{python}","%s","--stage","{stage}","--spec","{spec}","--out","{out}","--seed","{seed}",'
               '"--attempt","{attempt}"]' % gym)
        text = text[:i] + cmd + text[j:]
    p = tmp_path / name
    p.write_text(text)
    return p


def run_cmd(contract, workdir, *extra, arm="evo", seed=3, max_evals=None) -> list[str]:
    cmd = CLI + ["run", "--contract", str(contract), "--workdir", str(workdir), "--arm", arm, "--seed", str(seed)]
    if max_evals is not None:
        cmd += ["--max-evals", str(max_evals)]
    return cmd + list(extra)


def wait_for(pred, timeout: float, step: float = 0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def query(workdir, sql, args=()):
    """Read the ledger from outside the daemon (as `ants top` would)."""
    p = Path(workdir) / "ledger.sqlite"
    if not p.exists():
        return []
    conn = sqlite3.connect(p, timeout=30)
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def descendants(pid: int) -> list[int]:
    out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        try:
            c, p = map(int, line.split())
        except ValueError:
            continue
        kids.setdefault(p, []).append(c)
    found, todo = [], [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            found.append(c)
            todo.append(c)
    return found


def alive(pid: int) -> bool:
    """True if the process exists and is not a zombie."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


@pytest.fixture
def procs():
    """Start daemons/helpers; whatever is left (including their children) is SIGKILLed at teardown."""
    started: list[subprocess.Popen] = []
    seen: set[int] = set()

    def start(cmd, **kw) -> subprocess.Popen:
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.STDOUT)
        p = subprocess.Popen(cmd, **kw)
        started.append(p)
        seen.add(p.pid)
        return p

    start.track = seen.add
    yield start
    for p in started:
        kids = descendants(p.pid) if p.poll() is None else []
        for pid in kids + [p.pid]:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        if p.stdout:
            p.stdout.close()
    for pid in seen:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
