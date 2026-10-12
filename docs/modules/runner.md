# runner

Runs one evaluation as a supervised subprocess.

**Interface**
- `run_command(cmd, out_dir, timeout_s, required, env_extra=None, fsize_mb=256, should_stop=None)` -> `RunResult(returncode, timed_out, wall_s, cpu_s, metrics, error, interrupted)`. `RunResult.signalled` = killed by a signal that might not repeat.
- `validate_metrics(m, required)`; constants `TOKEN_ENV = "ANTS_TRIAL_TOKEN"`, `MAX_METRICS_BYTES` (1 MiB), `MAX_METRIC_KEYS` (512), `INT_LIMIT` (2^63), `KILL_WAIT_S` (30).
- Environment of the child: `PATH`, `HOME=<out_dir>`, `PYTHONHASHSEED=0`, single-threaded BLAS/OpenMP variables, `PYTHONPATH` (copied), `env_extra`, and a fresh `ANTS_TRIAL_TOKEN` per run. Nothing else of the parent's environment (no API keys).

**Invariants**
- Own process group. Timeout kills the whole group (TERM, 3 s, KILL); never polls `killpg(pid, 0)` (EPERM on macOS zombies). Timeouts and wall time use the monotonic clock.
- `should_stop` is polled every 0.25 s: the group is killed and the result is `interrupted=True` (not evidence; the campaign requeues the trial with reason `shutdown`). A child that survives SIGKILL for 30 s returns an error instead of hanging the daemon.
- Escapees: after every run, processes that carry this run's `ANTS_TRIAL_TOKEN` in their environment are SIGKILLed even if they left the group (`setsid`, double fork). Linux reads `/proc/<pid>/environ`; macOS parses `ps -Eww`. Tested: macOS live with python children only; the Linux path only against a synthetic `/proc` tree.
- Limits: `RLIMIT_CPU` (2 x timeout + 5 s), `RLIMIT_FSIZE` (256 MB per file), `RLIMIT_CORE=0`. SIGXFSZ and SIGXCPU are deterministic, so they are plain failures, not `signalled`.
- `metrics.json` (stale one deleted before the run): must be a regular file (no FIFO, `O_NOFOLLOW`, non-blocking open), at most 1 MiB, valid UTF-8 JSON without NaN/Infinity, at most 512 keys, and a flat dict in which EVERY value is a non-bool finite int or float (ints below 2^63). One bad key rejects the file and is named in the error. The required keys must be present.
- stdout/stderr go to files; the error text reads only the last 400 bytes with a seek.

**Notes / failure behaviour**
- Signal-killed children are `signalled` (retryable up to `max_attempts`); timeouts, bad metrics, SIGXFSZ/SIGXCPU and non-zero exits are terminal.
- A daemon `kill -9` does not stop a running gym by itself; under systemd `KillMode=control-group` cleans it up, and a restarted daemon requeues the row. `PR_SET_PDEATHSIG` is not used.
- Memory is not limited here (no `RLIMIT_AS`); the systemd cgroup and the contract's `static_mem` pre-filter do that.
