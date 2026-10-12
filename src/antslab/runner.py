"""Run one evaluation as a supervised subprocess.

- own process group, so a timeout kills grandchildren too; a grandchild that escaped it (setsid, double fork) is found by the
  per-trial token in its environment and killed as well (Linux: /proc, macOS: `ps -E`)
- RLIMIT_CPU / RLIMIT_FSIZE as a second line of defence (memory is enforced by the systemd cgroup), no core dumps
- stdout/stderr go to files, never into the supervisor's memory (the error tail is read with a seek)
- metrics.json is read without blocking and without trusting its size, then validated: a flat dict of finite numbers
- `should_stop` lets the daemon abandon a running trial within a fraction of a second (SIGTERM/SIGINT)
"""
from __future__ import annotations

import json
import math
import os
import resource
import shutil
import signal
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

TOKEN_ENV = "ANTS_TRIAL_TOKEN"
MAX_METRICS_BYTES = 1 << 20          # a flat dict of numbers; anything bigger is a bug or an attack
MAX_METRIC_KEYS = 512
INT_LIMIT = 1 << 63                  # metrics are doubles in spirit; a 400-digit integer is not a measurement
KILL_WAIT_S = 30                     # after SIGKILL: a child stuck in uninterruptible sleep must not hang the daemon forever


@dataclass
class RunResult:
    returncode: int | None
    timed_out: bool
    wall_s: float
    cpu_s: float
    metrics: dict | None
    error: str | None
    interrupted: bool = False        # abandoned because the daemon was asked to stop: the trial is NOT a result

    @property
    def signalled(self) -> bool:
        """Killed by a signal that might not repeat (OOM killer, kill -9). Hitting our own file-size / CPU limits is
        deterministic, so those are plain failures: retrying them only burns another 256 MB of disk."""
        return (self.returncode is not None and self.returncode < 0 and not self.timed_out and not self.interrupted
                and -self.returncode not in (signal.SIGXFSZ, signal.SIGXCPU))


def _limits(timeout_s: int, fsize_mb: int):
    def fn():
        resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_s * 2) + 5, int(timeout_s * 2) + 10))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_mb << 20, fsize_mb << 20))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))          # a crashing 800 MB gym must not write an 800 MB core file
    return fn


def _killpg(pgid: int, sig: int):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):      # PermissionError: macOS reports EPERM for a group of only zombies
        pass


# ---- escapees: processes that left the process group but still carry this trial's token -------------------------
def _token_pids(token: str, proc_root: str = "/proc") -> list[int]:
    needle = f"{TOKEN_ENV}={token}"
    me, found = os.getpid(), []
    if os.path.isdir(os.path.join(proc_root, "self")):                 # Linux
        want = b"\0" + needle.encode() + b"\0"           # delimited on both sides: not a suffix of another variable
        for name in os.listdir(proc_root):
            if not name.isdigit() or int(name) == me:
                continue
            try:
                with open(os.path.join(proc_root, name, "environ"), "rb") as f:
                    if want in b"\0" + f.read(4 << 20):
                        found.append(int(name))
            except OSError:                                           # gone, or not ours
                continue
        return found
    try:                                                              # macOS / BSD: `ps -E` appends the environment
        ps = subprocess.Popen(["ps", "-Eww", "-x", "-o", "pid=,command="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, errors="replace")
    except OSError:
        return []
    watchdog = threading.Timer(10, ps.kill)                           # a wedged ps must not wedge the daemon
    watchdog.start()
    try:
        for line in ps.stdout:                                        # streamed: the whole table is several MB
            if needle in line:
                try:
                    pid = int(line.split(None, 1)[0])
                except (ValueError, IndexError):
                    continue
                if pid != me:
                    found.append(pid)
    finally:
        watchdog.cancel()
        ps.kill()
        ps.wait()
        ps.stdout.close()
    return found


def _kill_stragglers(token: str):
    for pid in _token_pids(token):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _kill_group(proc: subprocess.Popen) -> bool:
    """SIGTERM the whole group, give the leader 3 s, then SIGKILL the group (catches grandchildren).
    Never poll killpg(pgid, 0): on macOS it raises EPERM once only a zombie leader remains.
    Returns False if the leader could not be reaped even after SIGKILL."""
    _killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    _killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False
    return True


def validate_metrics(m, required: list[str]) -> str | None:
    """E0: a flat dict of finite numbers (every key, not only the required ones: nested objects and NaN would otherwise be
    stored verbatim in the ledger and the hash chain)."""
    if not isinstance(m, dict):
        return "metrics.json is not an object"
    if len(m) > MAX_METRIC_KEYS:
        return f"metrics.json has {len(m)} keys"
    for k, v in m.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"metric {k!r} is not a number"
        if isinstance(v, int):
            if abs(v) >= INT_LIMIT:
                return f"metric {k!r} is out of range"
        elif not math.isfinite(v):
            return f"metric {k!r} is non-finite"
    for k in required:
        if k not in m:
            return f"metric {k!r} missing or non-finite"
    return None


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name}")


def _read_metrics(mp: Path) -> tuple[object, str | None]:
    """(parsed json, None) or (None, error). Never blocks (FIFO), never follows a symlink, never reads more than
    MAX_METRICS_BYTES."""
    try:
        st = os.lstat(mp)
    except FileNotFoundError:
        return None, "no metrics.json"
    except OSError as e:
        return None, f"metrics.json unreadable: {e.strerror}"
    if not stat.S_ISREG(st.st_mode):
        return None, "metrics.json is not a regular file"
    if st.st_size > MAX_METRICS_BYTES:
        return None, f"metrics.json too large ({st.st_size} bytes)"
    try:
        fd = os.open(mp, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as e:
        return None, f"metrics.json unreadable: {e.strerror}"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):                    # swapped between lstat and open
            return None, "metrics.json is not a regular file"
        data = os.read(fd, MAX_METRICS_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_METRICS_BYTES:
        return None, "metrics.json too large"
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_reject_constant), None
    except Exception as e:                    # bad UTF-8, bad JSON, RecursionError on absurd nesting, 4300-digit integers
        return None, f"bad metrics.json: {type(e).__name__}: {str(e)[:200]}"


def _tail(path: Path, n: int = 400) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - n))
            return f.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


def run_command(cmd: list[str], out_dir: Path, timeout_s: int, required: list[str],
                env_extra: dict | None = None, fsize_mb: int = 256, should_stop=None) -> RunResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    mp = out_dir / "metrics.json"
    try:                                            # a reused directory must never let `exit 0` pass on an old result
        os.unlink(mp)
    except FileNotFoundError:
        pass
    except OSError:
        shutil.rmtree(mp, ignore_errors=True)
    token = uuid.uuid4().hex
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(out_dir), "PYTHONHASHSEED": "0",
           "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
           "VECLIB_MAXIMUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
           "PYTHONUNBUFFERED": "1", "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    env.update(env_extra or {})
    env[TOKEN_ENV] = token
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()                           # not time.time(): an NTP step must not distort or shorten a timeout
    timed_out = interrupted = unkillable = False
    with open(out_dir / "stdout.log", "wb") as so, open(out_dir / "stderr.log", "wb") as se:
        p = subprocess.Popen(cmd, stdout=so, stderr=se, stdin=subprocess.DEVNULL, cwd=out_dir,
                             env=env, start_new_session=True, preexec_fn=_limits(timeout_s, fsize_mb))
        try:
            while True:
                try:
                    p.wait(timeout=max(0.01, min(0.25, t0 + timeout_s - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if should_stop is not None and should_stop():
                        interrupted = True
                        break
                    if time.monotonic() - t0 >= timeout_s:
                        timed_out = True
                        break
            if timed_out or interrupted:
                unkillable = not _kill_group(p)
        finally:
            if p.poll() is None:                    # an exception (e.g. the daemon's own shutdown) must not orphan the gym
                _killpg(p.pid, signal.SIGKILL)
                try:
                    p.wait(timeout=KILL_WAIT_S)
                except subprocess.TimeoutExpired:
                    pass
            _killpg(p.pid, signal.SIGKILL)          # stragglers in the group
    wall = time.monotonic() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    _kill_stragglers(token)                         # ... and those that left the group (setsid / double fork)
    if unkillable:
        return RunResult(None, timed_out, wall, cpu, None, f"child survived SIGKILL for {KILL_WAIT_S}s (uninterruptible)", interrupted)
    if interrupted:
        return RunResult(p.returncode, False, wall, cpu, None, "interrupted: daemon is stopping", True)
    if timed_out:
        return RunResult(p.returncode, True, wall, cpu, None, f"timeout after {timeout_s}s")
    if p.returncode != 0:
        if p.returncode == -signal.SIGXFSZ:
            why = f"exceeded the {fsize_mb} MB output file size limit (SIGXFSZ)"
        elif p.returncode == -signal.SIGXCPU:
            why = "exceeded the CPU time limit (SIGXCPU)"
        else:
            why = f"exit {p.returncode}: {_tail(out_dir / 'stderr.log')}"
        return RunResult(p.returncode, False, wall, cpu, None, why)
    m, err = _read_metrics(mp)
    if err is None:
        err = validate_metrics(m, required)
    if err:
        return RunResult(0, False, wall, cpu, None, err)
    return RunResult(0, False, wall, cpu, m, None)
