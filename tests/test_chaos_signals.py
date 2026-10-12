"""SIGTERM/SIGINT while a trial is running (systemd gives 60 s, KillMode=control-group then SIGKILLs)."""
import os
import signal
import socket
import threading
import time

import pytest

from tests.test_chaos_common import STUBBORN_GYM, alive, descendants, procs, query, run_cmd, wait_for, write_contract  # noqa: F401


def _running(wd):
    return query(wd, "SELECT COUNT(*) FROM trials WHERE state='running'")[0][0] if (wd / "ledger.sqlite").exists() else 0


def _check_clean_stop(wd, kids):
    assert not any(alive(k) for k in kids), "the gym's process group outlived the daemon"
    assert query(wd, "SELECT COUNT(*) FROM trials WHERE state='running'")[0][0] == 0, "a row is stuck in 'running'"
    from antslab.ledger import Ledger
    assert Ledger(wd / "ledger.sqlite").verify_chain()[0]
    q = query(wd, "SELECT COUNT(*) FROM trials WHERE state='queued'")[0][0]
    assert q >= 1                                                        # the interrupted trial went back to the queue
    ev = query(wd, "SELECT payload FROM events WHERE kind='trial_requeued'")
    assert ev and "shutdown" in ev[-1][0]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_stop_signal_mid_trial_stops_fast_and_requeues(tmp_path, procs, sig):
    contract, wd = write_contract(tmp_path, timeout=300), tmp_path / "wd"
    d = procs(run_cmd(contract, wd, max_evals=1000), env={**os.environ, "TOY_SLEEP": "120"})
    assert wait_for(lambda: _running(wd) and descendants(d.pid), 30), "no trial started"
    kids = descendants(d.pid)
    t = time.time()
    d.send_signal(sig)
    try:
        out, _ = d.communicate(timeout=8)
    except Exception:
        pytest.fail("daemon still running 8 s after the stop signal (systemd would wait 60 s and SIGKILL)")
    assert time.time() - t < 8 and d.returncode == 0, out
    _check_clean_stop(wd, kids)


def test_stop_signal_with_sigterm_ignoring_gym(tmp_path, procs):
    gym = tmp_path / "stubborn.py"
    gym.write_text(STUBBORN_GYM)
    contract, wd = write_contract(tmp_path, gym=gym, timeout=300), tmp_path / "wd"
    d = procs(run_cmd(contract, wd, max_evals=1000))
    assert wait_for(lambda: _running(wd) and descendants(d.pid), 30)
    kids = descendants(d.pid)
    t = time.time()
    d.send_signal(signal.SIGTERM)
    try:
        out, _ = d.communicate(timeout=15)
    except Exception:
        pytest.fail("daemon did not stop within 15 s")
    assert time.time() - t < 12 and d.returncode == 0, out
    _check_clean_stop(wd, kids)


def test_stop_signal_while_blocked_in_an_llm_call(tmp_path, procs):
    """No trial is running, the daemon is stuck in a (100 s timeout) network call: it must still stop promptly."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held = []

    def accept_forever():
        try:
            while True:
                held.append(srv.accept())                    # accept and never answer
        except OSError:
            pass                                             # socket closed at the end of the test
    threading.Thread(target=accept_forever, daemon=True).start()
    env_file = tmp_path / "llm.env"
    env_file.write_text(f"LLM_A_BASE=http://127.0.0.1:{srv.getsockname()[1]}/v1\nLLM_A_KEY=k\nLLM_A_MODEL=m\n")
    env_file.chmod(0o600)
    contract, wd = write_contract(tmp_path), tmp_path / "wd"
    d = procs(run_cmd(contract, wd, "--llm-env", str(env_file), arm="llm", max_evals=10))
    assert wait_for(lambda: held, 30), "the daemon never reached the LLM call"
    time.sleep(0.5)
    t = time.time()
    d.send_signal(signal.SIGTERM)
    try:
        out, _ = d.communicate(timeout=10)
    except Exception:
        pytest.fail("daemon stuck in the LLM call after SIGTERM")
    srv.close()
    assert time.time() - t < 10 and d.returncode == 0, out
    assert query(wd, "SELECT COUNT(*) FROM trials WHERE state='running'")[0][0] == 0


def test_normal_finish_does_not_wait_for_an_llm_call_in_flight(tmp_path, procs):
    """The budget is reached while a proposer thread sits in a never-answered network call: the process must exit at once, not
    after the call's 100 s timeout (found on Linux CI: the interpreter joins non-daemon threads at exit)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held = []

    def accept_forever():
        try:
            while True:
                held.append(srv.accept())
        except OSError:
            pass
    threading.Thread(target=accept_forever, daemon=True).start()
    env_file = tmp_path / "llm.env"
    env_file.write_text(f"LLM_A_BASE=http://127.0.0.1:{srv.getsockname()[1]}/v1\nLLM_A_KEY=k\nLLM_A_MODEL=m\n")
    env_file.chmod(0o600)
    contract, wd = write_contract(tmp_path), tmp_path / "wd"
    d = procs(run_cmd(contract, wd, "--llm-env", str(env_file), arm="llm", max_evals=4))
    assert wait_for(lambda: held, 30), "the daemon never reached the LLM call"
    try:
        out, _ = d.communicate(timeout=30)
    except Exception:
        pytest.fail("the daemon finished its budget but did not exit while an LLM call was in flight")
    srv.close()
    assert d.returncode == 0 and "budget" in out.decode(errors="replace"), out


def test_stop_signal_during_a_long_nap(tmp_path, procs):
    """With the breaker open the loop naps for 60 s: a stop signal must not wait for it."""
    contract, wd = write_contract(tmp_path), tmp_path / "wd"
    d = procs(run_cmd(contract, wd, max_evals=100), env={**os.environ, "TOY_FAULT": "exit", "TOY_FAULT_MOD": "1"})
    assert wait_for(lambda: query(wd, "SELECT COUNT(*) FROM trials WHERE state='failed'")[0][0] >= 3 if (wd / "ledger.sqlite").exists() else 0, 60)
    time.sleep(1.5)                                                      # the breaker is open and the loop is asleep
    t = time.time()
    d.send_signal(signal.SIGTERM)
    try:
        out, _ = d.communicate(timeout=10)
    except Exception:
        pytest.fail("daemon kept napping after SIGTERM")
    assert time.time() - t < 8 and d.returncode == 0, out
