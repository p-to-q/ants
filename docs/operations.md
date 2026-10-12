# Operations: an unattended run on 2 vCPU / 2 GB

Target: Ubuntu 24.04, 2 vCPU, 2 GB RAM, 2 GB swap as a buffer. Status: `experimental`. The units and scripts have **not been run on the target
machine** ([surfaces.md](surfaces.md)); numbers below were measured on a macOS arm64 development machine unless a row says otherwise.
Files: `deploy/install.sh`, `deploy/remote_deploy.sh`, `deploy/PREFLIGHT.md`, `deploy/ants@.service`, `ants-janitor.*`, `ants-health.*`, `ants-board.service`.

## 1. Install

| step | command | what it does |
|---|---|---|
| 0 | read `deploy/PREFLIGHT.md` | SSH keys only, firewall (never expose 8765), updates, 2 GB swap, secrets mode 600 owner `ants`, `systemd-analyze security` check. The installer does none of this |
| 1 | `ANTS_HOST=... ANTS_USER=... ANTS_KEY=... deploy/remote_deploy.sh preflight` | read-only: cpu, memory, disk, python, sudo, listening ports |
| 2 | `deploy/remote_deploy.sh install` | uploads the newest `dist/antslab-*.tar.gz`, checks its SHA-256 on the server, runs `install.sh`, installs `contracts/tinyts_real.toml` as `/opt/ants/contracts/tinyts.toml` |
| 3 | `deploy/remote_deploy.sh data` | fetches the dev-tier datasets on the server, builds `etth1 ettm1 weather exchange`, `ants data verify --partial` |
| 4 | `deploy/remote_deploy.sh smoke 60` | 60 evaluations as user `ants` into `/var/lib/ants/smoke`, then `top --once` and `doctor` |
| 5 | `deploy/remote_deploy.sh start` | `systemctl enable --now ants@portfolio ants-janitor.timer ants-health.timer` |

`install.sh` (root, Ubuntu 24.04): refuses if `/var` has < 15 GB free or the source is not an ants tree; creates system user `ants` (home `/var/lib/ants`);
rsyncs code to `/opt/ants` **excluding** `.venv runs *.sqlite* *.env data/raw data/processed data/MANIFEST.dev.sha256 contracts/tinyts.toml`; builds the venv from the hashed lock
`requirements/server.txt` (`--require-hashes`); installs six units; warns if `/etc/ants/llm.env` is not `600 ants`. It starts nothing.

Not run: `remote_deploy.sh` (no server). `install.sh` was read, not executed.
`remote_deploy.sh install` globs `dist/antslab-*.tar.gz`: build the package with `scripts/pack_public.sh` first.

## 2. Units and limits

| unit | key limits | note |
|---|---|---|
| `ants@<arm>.service` | `CPUQuota=150%`, `MemoryHigh=900M`, `MemoryMax=1100M`, `OOMPolicy=kill`, `OOMScoreAdjust=500`, `TasksMax=256`, `Restart=always`, `RestartPreventExitStatus=3`, `RestartSec=30`, `StartLimitIntervalSec=0`, `TimeoutStopSec=60`, `KillMode=control-group` | one instance per arm; workdir `/var/lib/ants/<arm>`; runs `ants run --seed 1 --llm-env /etc/ants/llm.env` with **no** `--max-evals` (forever). Sandboxed: `ProtectSystem=strict`, writes only `/var/lib/ants`, no capabilities, `UMask=0077` |
| `ants-janitor.timer` -> `.service` | first run 10 min after boot, then every 30 min; `CPUQuota=50%`, `MemoryMax=300M`; `InaccessiblePaths=/etc/ants` | `ants janitor --archive-dir /var/lib/ants/archive` |
| `ants-health.timer` -> `.service` | first run 20 min after boot, then every 15 min; `MemoryMax=200M`; no network | `ants doctor`; exit 2 puts the unit in `systemctl --failed` |
| `ants-board.service` | `CPUQuota=25%`, `MemoryMax=200M`, `IPAddressDeny=any`, `IPAddressAllow=localhost` | optional; `ssh -N -L 8765:127.0.0.1:8765 user@host` |

Deliberately absent (comment in the unit): `LimitFSIZE` (would kill the supervisor when `ledger.sqlite` grows past it; the runner gives each **child**
256 MiB), `MemoryDenyWriteExecute`, `SystemCallFilter`, `ProcSubset`. Child processes get BLAS threads = 1, so the 150% quota is about one busy core plus headroom.

Only one `ants run` may use a workdir: `flock` on `<workdir>/ants.lock` (`campaign.InstanceLock`). A second start prints `another ants run (pid N) already holds ...` and exits 3 (verified). `ants@.service` sets `RestartPreventExitStatus=3`, so the duplicate unit stays stopped instead of retrying.

## 3. Disk levels, PAUSE, breaker

| level | free space (defaults, contract `[disk]`) | what the code does |
|---|---|---|
| ok | >= 15 GB | nothing |
| warn | < 15 GB | `doctor` WARN; `ants janitor` computes `need = (warn - free)` and tries to free that much |
| clean | < 12 GB | `doctor` FAIL; same janitor behaviour |
| pause | < 8 GB | `Campaign.step` returns `paused:disk:pause`: **no new work**; the loop sleeps 60 s and re-checks, so it resumes by itself |
| hardstop | < 6 GB | identical to pause in `campaign.py` (`paused:disk:hardstop`) |

| control | how | effect |
|---|---|---|
| PAUSE flag | `ants pause` / `ants resume` (file `<workdir>/PAUSE`) | `paused:user`; the current evaluation finishes, nothing new starts; re-checked every 5 s |
| breaker | automatic | the 3 newest finished trials are all `failed/timeout/crashed` and the newest finished < 3600 s ago -> step returns `breaker`; the loop sleeps 60 s per check (in 0.5 s slices, so SIGTERM is not delayed) and continues by itself after the cooldown, measured on the monotonic clock from the moment the latest failure was seen. Test: `test_persistent_failure_opens_breaker` |
| recovery | on every start | `Campaign.recover()` reclaims every `running` row: requeued if `attempt < max_attempts`, else `crashed`; event `recovered`. Test: `test_recover_after_simulated_crash`, kill -9 chaos test (`pytest -m slow`) |
| stop | SIGTERM / SIGINT | a running evaluation is abandoned within about 0.25 s (group killed, nothing recorded as a result, row reclaimed at the next start), then exit 0. This fits inside `TimeoutStopSec=60`, even with an s2 timeout of 1800 s. Not exercised under systemd |

## 4. Janitor, archive, backup, anchoring

| thing | fact |
|---|---|
| what is archived | every registered artifact (`spec.json`, `metrics.json`, gzipped logs of full-record stages and of failures) to `--archive-dir`, verified by re-reading and hashing (`archive.archive_pending`, 200 per call) |
| what may be deleted | only artifacts with `protected=0`, kind not in `{ledger, manifest, config, model_best, report}`, archived and verified, older than 6 h, trial not queued/running, file hash re-checked at delete time (`janitor.plan_cleanup`). `metrics.json` and `spec.json` are registered **protected**, so in practice only gzipped logs are released |
| `/var/lib/ants/archive` | the same disk: it is a staging area, **not a backup** |
| S3 backend | `S3Archive` exists, no CLI flag selects it: `stub` |

Backup of the ledger (the only irreplaceable file; WAL mode, so do not `cp` it while running). The SQLite online-backup API was verified on a copy
of the demo ledger (`integrity_check` = `ok`, 140 events):

```bash
python3 - <<'EOF'
import sqlite3, time
s = sqlite3.connect("/var/lib/ants/portfolio/ledger.sqlite")
d = sqlite3.connect(f"/var/lib/ants/backup-{time.strftime('%Y%m%d')}.sqlite"); s.backup(d); d.close()
EOF
```

Off-box anchoring: the hash chain detects edits inside the chain but not truncation or a consistent rewrite; only a copy of the head that the writer cannot edit does
(`ledger.py` docstring, [E0](evaluation/E0-integrity.md)). There is no CLI for it. Verified on the demo copy:

```python
from antslab.ledger import Ledger
l = Ledger("/var/lib/ants/portfolio/ledger.sqlite")
l.write_anchor("/var/lib/ants/anchors.jsonl")        # appends {"seq":139,"head":"166b56...","ts":...}
l.verify_anchor_file("/var/lib/ants/anchors.jsonl")   # (True, None); (False, "truncated: ...") if the tail is gone
```

Copy `anchors.jsonl` off the machine (mail, a git remote, object lock) at every freeze and after every sealed look. `gate.seal(..., anchor_file=...)` refuses to run if the
ledger no longer contains every anchored head. `ants status` prints the head (16 hex characters) if you only want to note it down.
Not run on the target machine: the backup and anchor commands.

## 5. What `ants doctor` checks

Seven checks, the thresholds are in [cli.md](cli.md#doctor). `ants-health.timer` runs it every 15 min. One caveat from the code: the freshness check fails when no trial finished for
30 min **while work is queued**, but an `s2` stage may legitimately run up to 1800 s (30 min) in `tinyts_real`, so a slow s2 can produce a transient FAIL.

## 6. Failure modes

| symptom | cause (from the code) | action |
|---|---|---|
| `top` shows `[PAUSED]`; doctor WARN `paused by the PAUSE flag file` | `<workdir>/PAUSE` exists | `ants resume` |
| no new trials, process alive, journal quiet | breaker open (3 consecutive bad finals, last < 1 h ago) or disk `pause` | `ants log -n 20 --kind trial_finished`; read `runs/<arm>/<id>_a<n>/stderr.log.gz`; fix the cause. Breaker clears itself 1 h after the last failure |
| every trial `failed`, `catalog.toml not found` or missing raw file | datasets absent on the server | `deploy/remote_deploy.sh data`; `ants data verify --partial` |
| `no contract found` | `/opt/ants/contracts/tinyts.toml` missing (it is excluded from rsync on purpose) | copy a contract there |
| disk under 8 GB, `paused:disk:pause` | trials and logs grew | `ants janitor --archive-dir ...`; delete old workdirs (`runs/smoke`); loop resumes within 60 s |
| `doctor` FAIL `hash chain broken at event N` / `ants status` exit 2 | edit or deletion inside the chain, disk corruption | stop the unit, keep the file, restore the newest backup, compare with `anchors.jsonl`; do not claim sealed looks from this ledger |
| process killed, service restarts after 30 s | OOM (`MemoryMax=1100M`, `OOMPolicy=kill`) or a crash | check `journalctl -u ants@portfolio`; lower `mem_cap_mb`; rows are reclaimed by `recover()`. Child killed by signal is retried, then `crashed` |
| doctor WARN `N trial(s) running past their lease` | runner died; the lease is `max(timeouts)+60` s | none; reclaimed at next start |
| doctor WARN `2 epoch(s) recorded` | code, contract or data manifest changed during the ledger's life (a redeploy rewrites the code hash) | expected after an upgrade; `History` ignores older epochs, so proposers restart from empty memory. Never compare numbers across epochs |
| `PermissionError: ... must be chmod 600` | `llm.env` has group/other bits | `chmod 600`, owner `ants`. The installer warns about this |
| LLM arm WARN parse rate <= 60% | model output not valid JSON for the grammar | rejected specs are counted, nothing breaks; consider `portfolio` without LLM |
| second service on the same workdir | `ants.lock` held; the second `ants run` exits 3 and systemd does not restart it (`RestartPreventExitStatus=3`) | disable the duplicate unit |
| `idle` forever | proposer returned nothing (space exhausted or all keys seen) | `ants top`; widen the space in a new contract (new epoch) |

## 7. Budget estimate

Measured on macOS arm64, `contracts/tinyts_real.toml`, default spec (420 parameters), one process, `nice -n 15`, with other jobs running on the machine:

| stage | what it evaluates | wall | peak RSS | source |
|---|---|---|---|---|
| s0 | 1 dataset, H=96, 1 fold | 0.20 s | 179 MiB | `/usr/bin/time -l` |
| s1 | 3 datasets, 4 H, 3 folds (36 tasks) | 4.95 s | 672 MiB | same |
| s2 | 4 datasets, 4 H, 3 folds (48 tasks) | 10.87 s | 745 MiB | same |

Demo ledger (`runs/demo`, 61 rows, same profile): median `cpu_s` s0 0.22 (n=21), s1 5.57 (n=20), s2 12.23 (n=19). A 12-evaluation campaign run
(5 s0, 4 s1, 3 s2) took 53.8 s wall; supervisor peak RSS 53 MiB, largest child 785 MiB.

| quantity | formula or value | status |
|---|---|---|
| seconds per proposal, steady state | `c0 + p*c1 + p*p*c2` with `p = promote_quantile = 0.25`: 0.20 + 0.25 x 4.95 + 0.0625 x 10.87 = 2.1 s | extrapolated; the demo was entirely warm-up (nearly every row promoted) |
| proposals per day, one worker | 86 400 / 2.1 = about 41 000 (plus about 10 000 s1 and 2 500 s2 rows: about 53 000 ledger rows per day) | extrapolated; assumes no idle time, p exactly at the quantile |
| memory per evaluation | peak 745 MiB at s2 + supervisor 53 MiB = about 800 MiB against `MemoryHigh=900M`, `MemoryMax=1100M` | macOS RSS; `est_mem_mb` says 560 for this spec, so it under-predicts by 33% here |
| ledger growth | about 2.1 KB of payload per row (38 174 B trials + 92 466 B events over 61 rows); file size 274 KB / 61 = 4.5 KB per row (small-file upper bound) -> 0.1 to 0.25 GB per day | extrapolated |
| artifact growth | 4 files per s2 row (`spec.json`, `metrics.json`, 2 logs), 15 887 B in total over 19 s2 rows; with 4 KB blocks about 16 KB per s2 row -> about 40 MB per day | extrapolated |
| CPU speed on the target | unknown: multiply the seconds by the ratio server/dev after running `deploy/remote_deploy.sh smoke 60` | Not measured on the target machine |
| peak RSS on Linux/2 GB | unknown | Not measured on the target machine |

`history.py` mentions "~200k trials/day" as a design figure; the measured profile above gives about 53 000 rows per day per worker, so do not plan on the larger number.
With about 0.3 GB/day of growth, a 40 GB disk with 25 GB free reaches the 15 GB warn level in roughly 30 days; the janitor cannot release the protected metrics files.
That sentence is arithmetic on extrapolated figures, not an observation.

## 8. Monthly check (from PREFLIGHT, 5 minutes)

```bash
ants status --contract /opt/ants/contracts/tinyts.toml --workdir /var/lib/ants/portfolio --arm portfolio   # chain_ok, head
df -h /var/lib/ants; ls -la /var/lib/ants/archive | tail -3
journalctl -u ants@portfolio --since "30 days ago" -p warning
systemctl --failed
```
