# campaign

The research loop for one arm: refill -> claim -> execute -> promote -> record, behind every safety gate.

**Interface**
- `Campaign.step()` does at most one evaluation and returns a status: `paused:user`, `budget`, `paused:disk:<level>`, `breaker`, `idle`, or `ran:<trial id>:<done|stale|interrupted|timeout|requeued|failed|crashed>`. `run(max_steps, stop, sleep)` owns sleeping, signals and error back-off.
- `InstanceLock(workdir)` / `AlreadyRunning`: `flock(2)` on `<workdir>/ants.lock`, taken by `ants run` before it opens the ledger. The kernel drops it when the process dies (also on kill -9) and gym children do not inherit it. The pid of the holder is written into the file. Only `run` takes it.
- Epoch = identity of the measurement machinery: SHA-256 over `code` (bytes of every file named in the contract's `code_paths`, a directory contributes its `*.py` files; a missing path raises), `contract` (hash of the whole parsed contract) and `data_manifest` (hash of `data/MANIFEST.sha256`, located relative to `code_paths[0]`). Package versions are recorded in the `epochs` table but are not part of the identity.
- Every trial is stamped with the epoch at CLAIM time. `History`, `seen_keys` (`_refill`) and `frontier()` / `freeze_best` are filtered to the campaign's epoch.
- Static-cost pre-filter: a free analytic cost (e.g. parameter count) screens out certain cap violations before any CPU is spent, except a small 'stepping-stone' quota (`stone_quota`). The memory pre-filter (`static_mem`, `mem_cap_mb`) has no quota.
- Per-stage timeouts (`[budget].stage_timeouts`); cascade promotion uses `History.stage_sorted` (no table scans); `evals_done` / `breaker_open` are SQL aggregates.
- Env of every gym run: `env_passthrough` variables from the contract, plus `ANTS_ALLOWED_ROLES=dev` set last (a contract cannot override it), so `data.Catalog.load` in the gym refuses confirm, sealed and stress data.
- `stats`: `static_rejected`, `stones_kept`, `mem_rejected`, `db_errors`, `os_errors`, `step_errors`.

**Invariants**
- Disk level `pause`/`hardstop` starts nothing; the breaker (`breaker_n` consecutive bad finishes, cooldown `breaker_cooldown_s`) is derived from the ledger and survives restarts. The cooldown runs on the monotonic clock from the moment the latest failure was seen; wall time is used once, to resume a cooldown that was running when the process started, and a future timestamp counts as "just happened".
- Start-up cost is small (the epoch is computed once; package versions are read from dist-info names, not importlib.metadata): a crash loop must not spend its time re-initialising.
- `recover()` re-queues trials left running by a dead process (`all_running=True`, safe because of the instance lock).
- Stop: SIGTERM/SIGINT set a flag; a running trial is noticed within 0.25 s and abandoned (process group gets SIGTERM, 3 s grace, then SIGKILL; trial requeued with reason `shutdown`, its half-result deleted); a blocking LLM or dream call, with nothing in flight, is abandoned through `Shutdown`; sleeps are sliced into 0.5 s chunks. The attempt counter is not refunded, so two kills during one trial can make it `crashed` ("runner lost").
- Transient errors (SQLite `locked`, `busy`, `disk is full`, `disk I/O`; ENOSPC, EIO, EMFILE, ENOMEM, EAGAIN...) never lose a finished evaluation: post-run writes are retried with back-off for about 75 s (`_db`); if they still fail the row stays `running` and start-up recovery re-evaluates it once. After any failed step the next healthy step reclaims orphaned `running` rows. `run()` backs off 0.5 s up to 60 s and writes a throttled `step_error` event. Five consecutive errors that are not transient exit non-zero. A trial that keeps crashing the supervisor is `crashed` after `max_attempts`.
- The promotion to the next stage is enqueued right after the `done` commit, before artifacts and logs are written.
- Relative paths: `workdir` is resolved to an absolute path (gyms run with the output directory as cwd).

**Notes / failure behaviour**
- `kill -9` of the whole process at random moments is tested (`-m slow`, 30+ kills, two seeds; chain valid, no duplicate (epoch, spec, stage), every `done` row has exactly one agreeing event).
- Finish and promotion are not one transaction: a kill between the `done` commit and the promotion `enqueue` loses that promotion (not duplicated, not corrupted; the window is milliseconds).
- The epoch is computed once, when the `Campaign` is built. Editing a gym file while the daemon runs changes what later trials execute but not their epoch until the process restarts.
- Epoch coverage depends on the contract: only files listed in `code_paths` count. `contracts/tinyts_real.toml` lists `tinyts.py`, `metrics.py`, `data.py`, `catalog.toml`, `catalog_synth.toml`, so a role/split change or an evaluator edit starts a new epoch there. The two synthetic contracts list only `tinyts.py`. `data/MANIFEST.sha256` is hashed, but raw files are not re-hashed at run time (only `ants data verify`), and `data/processed/*.npz` is not part of the identity. `Catalog.protocol_hash()` exists but the epoch does not use it.
- `freeze_best(cap)` freezes only current-epoch evidence and records the epoch, but not the data protocol hash. No CLI command calls `freeze_best` or `gate.seal`.
- A daemon `kill -9` leaves its gym running until systemd (`KillMode=control-group`) cleans up.
- Other consumers of the same ledger are not epoch-filtered: `ants best/top/show`, the board, `report.facts`, `dream`, `credit`, the plain `tpe` arm and the LLM proposers' prompt history read every epoch (see [history](history.md)).
- Dream, hint and LLM calls happen inside `step()`; they do not hold a trial.
