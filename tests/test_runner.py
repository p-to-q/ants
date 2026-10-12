import os, sys, time
from pathlib import Path
from antslab.runner import run_command, validate_metrics


def _py(tmp_path, code):
    p = tmp_path / "s.py"; p.write_text(code); return [sys.executable, str(p)]


def test_timeout_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "child.pid"
    cmd = _py(tmp_path, f"""
import subprocess, time
c = subprocess.Popen(["sleep", "300"]); open({str(pidfile)!r}, "w").write(str(c.pid)); time.sleep(300)
""")
    t = time.time()
    r = run_command(cmd, tmp_path / "o", 2, ["x"])
    assert r.timed_out and time.time() - t < 15
    time.sleep(0.3)
    pid = int(pidfile.read_text())
    try:
        os.kill(pid, 0); alive = True
    except ProcessLookupError:
        alive = False
    assert not alive


def test_nan_metric_rejected(tmp_path):
    cmd = _py(tmp_path, "import json,sys; json.dump({'x': float('nan')}, open('metrics.json','w'))")
    r = run_command(cmd, tmp_path / "o", 10, ["x"])
    assert r.metrics is None and "non-finite" in r.error


def test_missing_metric_and_nonzero_exit(tmp_path):
    r = run_command(_py(tmp_path, "import json; json.dump({'y': 1}, open('metrics.json','w'))"), tmp_path / "o1", 10, ["x"])
    assert r.metrics is None
    r = run_command(_py(tmp_path, "raise SystemExit(3)"), tmp_path / "o2", 10, ["x"])
    assert r.returncode == 3 and "exit 3" in r.error


def test_sigkilled_child_is_flagged_as_signalled(tmp_path):
    r = run_command(_py(tmp_path, "import os,signal; os.kill(os.getpid(), signal.SIGKILL)"), tmp_path / "o", 10, ["x"])
    assert r.signalled and not r.timed_out


def test_validate_metrics():
    assert validate_metrics({"x": 1.0}, ["x"]) is None
    assert validate_metrics({"x": True}, ["x"]) is not None
    assert validate_metrics([], ["x"]) is not None
