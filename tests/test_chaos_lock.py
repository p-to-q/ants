"""Single-instance protection: two `ants run` on one workdir must never both run (the second one would
`recover()` -- i.e. requeue -- the first one's live trial and evaluate it twice)."""
import os
import signal
import subprocess
import time

from tests.test_chaos_common import alive, descendants, procs, query, run_cmd, wait_for, write_contract  # noqa: F401


def test_second_instance_refuses_with_exit_3(tmp_path, procs):
    contract, wd = write_contract(tmp_path), tmp_path / "wd"
    env = {**os.environ, "TOY_SLEEP": "5"}
    first = procs(run_cmd(contract, wd, max_evals=1000), env=env)
    assert wait_for(lambda: (wd / "ledger.sqlite").exists() and query(wd, "SELECT COUNT(*) FROM trials WHERE state='running'")[0][0] > 0, 30), "first never started a trial"
    t = time.time()
    second = procs(run_cmd(contract, wd, max_evals=1000), env=env)
    try:
        out, _ = second.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        raise AssertionError("second instance kept running instead of refusing")
    assert time.time() - t < 10
    assert second.returncode == 3, out
    assert b"already" in out.lower() and str(first.pid).encode() in out      # names the holder
    assert first.poll() is None                                                # the first one is undisturbed
    # and the first one's in-flight trial was not touched by the refused instance
    assert query(wd, "SELECT COUNT(*) FROM trials WHERE state='running'")[0][0] == 1
    assert not query(wd, "SELECT 1 FROM events WHERE kind IN ('recovered','trial_reclaimed')")


def test_lock_dies_with_the_process_and_is_not_inherited_by_gym_children(tmp_path, procs):
    """kill -9 the daemon while its gym child is still alive: a new instance must be able to start at once
    (flock is released by the kernel, and the child must not have inherited the lock fd)."""
    # The child deliberately sleeps 30 s; give it a longer contract deadline so slow CI hosts do not timeout and kill it
    # before the daemon is force-killed below.
    contract, wd = write_contract(tmp_path, timeout=120), tmp_path / "wd"
    env = {**os.environ, "TOY_SLEEP": "30"}
    first = procs(run_cmd(contract, wd, max_evals=1000), env=env)

    def running_gym_children():
        # `descendants()` can observe the forked Python process before it has exec'd the gym module; killing the daemon in that
        # fork/exec window makes the test platform-dependent (macOS may reap the not-yet-exec'd child with its parent).
        pids = descendants(first.pid)
        for pid in pids:
            r = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
            if "antslab.gyms.toy" in r.stdout:
                return [pid]
        return False

    kids = wait_for(running_gym_children, 30)
    assert kids, "no gym child reached its running module"
    first.send_signal(signal.SIGKILL)
    first.wait()
    assert any(alive(k) for k in kids), "test premise: the orphaned gym child is still running"
    env2 = {**os.environ, "TOY_SLEEP": "0"}
    second = procs(run_cmd(contract, wd, max_evals=3), env=env2)
    out, _ = second.communicate(timeout=60)
    assert second.returncode == 0, out
    assert b"budget" in out
    for k in kids:
        try:
            os.kill(k, signal.SIGKILL)
        except ProcessLookupError:
            pass
