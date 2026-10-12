"""Runner under hostile gyms: output floods, garbage metrics, escapees, stubborn children, leaks."""
import json
import os
import subprocess
import sys
import textwrap
import time
import tracemalloc

import pytest

from antslab.runner import run_command
from tests.test_chaos_common import alive


def _py(tmp_path, code):
    p = tmp_path / "gym.py"
    p.write_text(textwrap.dedent(code))
    return [sys.executable, str(p)]


def _metrics(tmp_path, body: str, name="o"):
    """Run a gym that writes `body` (a python expression for the raw text) as metrics.json and exits 0."""
    return run_command(_py(tmp_path, f"open('metrics.json','w').write({body})"), tmp_path / name, 20, ["x"])


# ---------------------------------------------------------------- metrics.json
def test_huge_int_metric_is_rejected_not_a_crash(tmp_path):
    """math.isfinite(10**400) raises OverflowError: that used to escape run_command and kill the daemon (row left 'running')."""
    r = _metrics(tmp_path, "'{\"x\": 1' + '0' * 400 + '}'")
    assert r.metrics is None and r.error


@pytest.mark.parametrize("body", [
    '{"x": 1, "y": NaN}', '{"x": 1, "y": Infinity}', '{"x": 1, "y": -Infinity}', '{"x": 1, "y": 1e999}',
    '{"x": 1, "y": {"a": 1}}', '{"x": 1, "y": [1, 2]}', '{"x": 1, "y": "text"}', '{"x": 1, "y": null}', '{"x": 1, "y": true}',
    '{"x": 1, "y": 36893488147419103232}', '{"x": 36893488147419103232}', '[1, 2]', '"x"', '1', 'null', '',
])
def test_metrics_must_be_a_flat_dict_of_finite_numbers(tmp_path, body):
    r = _metrics(tmp_path, repr(body))
    assert r.metrics is None and r.error, body


def test_normal_metrics_still_pass(tmp_path):
    r = _metrics(tmp_path, repr('{"x": 1, "y": 2.5, "z": -3, "big": 9007199254740993, "tiny": 1e-300}'))
    assert r.error is None and r.metrics["x"] == 1


def test_huge_metrics_file_is_rejected_without_being_read(tmp_path):
    cmd = _py(tmp_path, "f = open('metrics.json', 'wb'); f.truncate(300 << 20); f.close()")
    tracemalloc.start()
    r = run_command(cmd, tmp_path / "o", 60, ["x"], fsize_mb=1024)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert r.metrics is None and "too large" in r.error
    assert peak < 20 << 20, f"supervisor allocated {peak >> 20} MB reading metrics.json"


def test_deeply_nested_metrics_are_just_rejected(tmp_path):
    r = _metrics(tmp_path, "'[' * 300000")
    assert r.metrics is None and r.error


def test_non_utf8_metrics_and_logs(tmp_path):
    r = _metrics(tmp_path, "b'{\"x\": \"\\xff\\xfe\"}'.decode('latin-1')")      # valid text, wrong type
    assert r.metrics is None
    cmd = _py(tmp_path, """
        import sys
        sys.stdout.buffer.write(b'\\xff\\xfe\\x80' * 1000); sys.stderr.buffer.write(b'\\xc3\\x28 bad \\x80')
        open('metrics.json', 'wb').write(b'{"x": 1}\\xff')
        raise SystemExit(0)
    """)
    r = run_command(cmd, tmp_path / "o2", 20, ["x"])
    assert r.metrics is None and r.error and isinstance(r.error, str)
    cmd = _py(tmp_path, "import sys; sys.stderr.buffer.write(b'\\xc3\\x28 bad \\x80'); raise SystemExit(2)")
    r = run_command(cmd, tmp_path / "o3", 20, ["x"])
    assert r.returncode == 2 and "bad" in r.error


def test_metrics_json_fifo_does_not_block_the_supervisor(tmp_path):
    """open() on a FIFO with no writer blocks forever: the daemon would hang with a row 'running'."""
    gym = tmp_path / "fifo_gym.py"
    gym.write_text("import os; os.mkfifo('metrics.json')")
    probe = tmp_path / "probe.py"
    probe.write_text(f"from pathlib import Path; from antslab.runner import run_command\n"
                     f"r = run_command([{sys.executable!r}, {str(gym)!r}], Path({str(tmp_path / 'o')!r}), 20, ['x'])\n"
                     f"print(r.metrics, r.error)\n")
    try:
        p = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail("run_command blocked on a FIFO named metrics.json")
    assert p.returncode == 0 and "None" in p.stdout, p.stderr


def test_metrics_json_symlink_is_not_followed(tmp_path):
    big = tmp_path / "elsewhere.bin"
    with open(big, "wb") as f:
        f.truncate(200 << 20)
    cmd = _py(tmp_path, f"import os; os.symlink({str(big)!r}, 'metrics.json')")
    tracemalloc.start()
    r = run_command(cmd, tmp_path / "o", 20, ["x"])
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert r.metrics is None and r.error and peak < 20 << 20


# ---------------------------------------------------------------- output floods
def test_stderr_flood_is_bounded_and_not_loaded_into_memory(tmp_path):
    cmd = _py(tmp_path, """
        import sys
        chunk = b'E' * 65536
        while True:
            sys.stderr.buffer.write(chunk); sys.stderr.buffer.flush()
    """)
    tracemalloc.start()
    t = time.time()
    r = run_command(cmd, tmp_path / "o", 60, ["x"], fsize_mb=32)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert time.time() - t < 30
    assert r.metrics is None and r.returncode != 0 and isinstance(r.error, str) and len(r.error) < 2000
    assert (tmp_path / "o" / "stderr.log").stat().st_size <= 32 << 20
    assert peak < 16 << 20, f"supervisor allocated {peak >> 20} MB just to report the error tail"


def test_stdout_flood_killed_by_sigxfsz_is_a_failure_not_a_retryable_crash(tmp_path):
    """A C/shell gym dies of SIGXFSZ at the file-size limit. That is deterministic: requeueing it is pointless."""
    r = run_command(["yes"], tmp_path / "o", 60, ["x"], fsize_mb=4)
    assert r.metrics is None and not r.timed_out
    assert not r.signalled, "SIGXFSZ was classified as a transient crash and would be retried"
    assert "file size" in r.error.lower() or "output" in r.error.lower()
    assert (tmp_path / "o" / "stdout.log").stat().st_size <= 4 << 20


def test_compress_logs_caps_enormous_logs(tmp_path):
    from antslab.archive import compress_logs
    import gzip
    (tmp_path / "stdout.log").write_bytes(b"x" * 100 + b"\n")
    with open(tmp_path / "stderr.log", "wb") as f:
        f.write(b"HEAD\n")
        f.seek(300 << 20)
        f.write(b"\nTAIL")
    t = time.time()
    made = compress_logs(tmp_path)
    assert time.time() - t < 20
    assert {m.name for m in made} == {"stdout.log.gz", "stderr.log.gz"}
    err = gzip.open(tmp_path / "stderr.log.gz").read()
    assert len(err) <= 17 << 20 and err.endswith(b"TAIL") and b"truncated" in err[:200]
    assert gzip.open(tmp_path / "stdout.log.gz").read() == b"x" * 100 + b"\n"


# ---------------------------------------------------------------- process hygiene
def _escapee(tmp_path, how):
    pidfile = tmp_path / "escapee.pid"
    if how == "setsid":
        body = f"""
            import subprocess, sys
            c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            open({str(pidfile)!r}, 'w').write(str(c.pid))
        """
    else:                                    # classic daemonisation: double fork + setsid, then sleep
        body = f"""
            import os, time
            if os.fork() == 0:
                os.setsid()
                if os.fork() == 0:
                    open({str(pidfile)!r}, 'w').write(str(os.getpid()))
                    os.close(1); os.close(2)
                    time.sleep(300)
                os._exit(0)
            os.wait()
            time.sleep(0.3)
        """
    return _py(tmp_path, textwrap.dedent(body) + "\nopen('metrics.json','w').write('{\"x\": 1}')\n"), pidfile


@pytest.mark.parametrize("how", ["setsid", "double_fork"])
def test_grandchild_that_escapes_the_process_group_is_killed(tmp_path, how):
    cmd, pidfile = _escapee(tmp_path, how)
    pid = None
    try:
        r = run_command(cmd, tmp_path / "o", 30, ["x"])
        assert r.error is None
        assert pidfile.exists(), "test premise: the escapee started"
        pid = int(pidfile.read_text())
        deadline = time.time() + 5
        while alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not alive(pid), "an escaped grandchild survived the trial and keeps eating the 2 vCPU box"
    finally:
        if pid is None and pidfile.exists():
            pid = int(pidfile.read_text())
        if pid:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def test_escapee_is_killed_after_a_timeout_too(tmp_path):
    cmd, pidfile = _escapee(tmp_path, "setsid")
    cmd = cmd[:1] + [str(tmp_path / "gym.py")]
    src = (tmp_path / "gym.py").read_text().replace("open('metrics.json','w').write('{\"x\": 1}')", "import time; time.sleep(300)")
    (tmp_path / "gym.py").write_text(src)
    pid = None
    try:
        r = run_command(cmd, tmp_path / "o", 2, ["x"])
        assert r.timed_out
        pid = int(pidfile.read_text())
        deadline = time.time() + 5
        while alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not alive(pid)
    finally:
        if pid:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def test_token_scan_reads_linux_proc_environ(tmp_path):
    """The /proc backend (production is Linux) exercised against a synthetic /proc."""
    from antslab.runner import _token_pids
    (tmp_path / "self").mkdir()
    for pid, env in ((101, b"PATH=/bin\0ANTS_TRIAL_TOKEN=tokA\0HOME=/x\0"), (102, b"ANTS_TRIAL_TOKEN=tokB\0"),
                     (103, b"XANTS_TRIAL_TOKEN=tokA\0"), (104, b"ANTS_TRIAL_TOKEN=tokAB\0")):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "environ").write_bytes(env)
    (tmp_path / "105").mkdir()                                        # unreadable / vanished: ignored
    (tmp_path / "net").mkdir()
    assert sorted(_token_pids("tokA", proc_root=str(tmp_path))) == [101]


def test_child_ignoring_sigterm_is_killed_on_timeout(tmp_path):
    cmd = _py(tmp_path, "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)")
    t = time.time()
    r = run_command(cmd, tmp_path / "o", 2, ["x"])
    assert r.timed_out and time.time() - t < 10


def test_exit_zero_without_metrics_is_a_failure(tmp_path):
    r = run_command(_py(tmp_path, "pass"), tmp_path / "o", 20, ["x"])
    assert r.metrics is None and r.error == "no metrics.json" and r.returncode == 0


def test_stale_metrics_from_a_previous_attempt_are_not_accepted(tmp_path):
    """A re-used output directory must not let `exit 0` pass on the previous attempt's metrics."""
    out = tmp_path / "o"
    out.mkdir()
    (out / "metrics.json").write_text('{"x": 1}')
    r = run_command(_py(tmp_path, "pass"), out, 20, ["x"])
    assert r.metrics is None


def test_should_stop_interrupts_a_running_gym_and_kills_its_group(tmp_path):
    pidfile = tmp_path / "kid.pid"
    cmd = _py(tmp_path, f"import subprocess, time; c = subprocess.Popen(['sleep', '300']); open({str(pidfile)!r}, 'w').write(str(c.pid)); time.sleep(300)")
    t0 = time.time()
    r = run_command(cmd, tmp_path / "o", 120, ["x"], should_stop=lambda: time.time() - t0 > 1.0)
    assert r.interrupted and r.metrics is None and time.time() - t0 < 10
    time.sleep(0.3)
    assert not alive(int(pidfile.read_text()))


def test_instant_exits_do_not_leak_fds_or_zombies(tmp_path):
    cmd = ["/bin/sh", "-c", "echo '{\"x\": 1}' > metrics.json"]
    run_command(cmd, tmp_path / "w0", 20, ["x"])
    before = len(os.listdir("/dev/fd"))
    for i in range(300):
        r = run_command(cmd, tmp_path / f"o{i % 5}", 20, ["x"])
        assert r.metrics == {"x": 1}
    assert len(os.listdir("/dev/fd")) == before
    with pytest.raises(ChildProcessError):
        os.waitpid(-1, os.WNOHANG)                       # nothing left to reap: no zombies


# ---------------------------------------------------------------- 500-trial soak (supervisor resources)
def _rss_mb():
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return int(out.strip()) / 1024


@pytest.mark.slow
def test_500_trials_do_not_leak_fds_or_grow_supervisor_memory(ledger, make_contract, tmp_path, capsys):
    from antslab.campaign import Campaign
    from antslab.proposers import make_proposer
    c = make_contract()
    cp = Campaign(ledger, c, tmp_path / "w", "evo", make_proposer("evo", c, 11), 11, budget_evals=500)
    marks = {}
    for n in range(2000):
        s = cp.step()
        done = cp.evals_done()
        if done >= 100 and "a" not in marks:
            marks["a"] = (len(os.listdir("/dev/fd")), _rss_mb(), done)
        if s == "budget":
            break
    marks["b"] = (len(os.listdir("/dev/fd")), _rss_mb(), cp.evals_done())
    with capsys.disabled():
        print(f"\n[soak] after {marks['a'][2]} trials: fds={marks['a'][0]} rss={marks['a'][1]:.1f} MB; "
              f"after {marks['b'][2]} trials: fds={marks['b'][0]} rss={marks['b'][1]:.1f} MB")
    assert marks["b"][2] >= 500
    assert marks["b"][0] <= marks["a"][0]
    assert marks["b"][1] - marks["a"][1] < 40
    with pytest.raises(ChildProcessError):
        os.waitpid(-1, os.WNOHANG)
    assert ledger.verify_chain()[0]
