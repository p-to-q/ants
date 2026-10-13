# CLI reference

Status: `stable` for the read commands and the shell (`tests/test_ui.py`), `experimental` for `board` / `serve`
(`tests/test_board.py`). Source: `src/antslab/cli.py`, `src/antslab/ui.py`. Every output below was produced on a copy of
`runs/demo` (60 finished trials, 1 queued, real-data catalog profile) with `contracts/tinyts_real.toml`, long values shortened.

```bash
.venv/bin/ants <command> [flags]        # or: PYTHONPATH=src .venv/bin/python -m antslab.cli <command> [flags]
.venv/bin/ants                          # no command: opens the interactive shell
.venv/bin/ants --version                # `ants <package version>`; `unknown` if not installed
```

## Common flags (accepted by every command except `data`)

| flag | default | meaning |
|---|---|---|
| `--contract PATH` | resolved, see below | contract TOML ([contracts.md](contracts.md)) |
| `--workdir DIR` | resolved | holds `ledger.sqlite`, `ants.lock`, `runs/`, `PAUSE`. Relative paths are resolved to absolute before use |
| `--arm NAME` | resolved | proposer arm; the ledger is filtered by it |
| `--seed N` | 0 | used by `run` only; accepted and ignored elsewhere |
| `--json` | off | machine output; honoured by `top best show log doctor` only (`status` is always JSON) |

## Flag-less resolution (`cli._resolve`)

First match wins. What was filled in is printed to stderr as `using contract=... workdir=... arm=...` (suppressed by `--json`).

| item | 1 | 2 | 3 | 4 | fallback |
|---|---|---|---|---|---|
| contract | `--contract` | `$ANTS_CONTRACT` | `[run] contract` in `~/.ants/config.toml` (written by `ants onboard`), else first existing of `/opt/ants/contracts/tinyts.toml`, `contracts/tinyts_real.toml`, `contracts/tinyts_synth_hard.toml` | | exit 1: `no contract found` |
| workdir | `--workdir` | `$ANTS_WORKDIR` | `[run] workdir` in `config.toml` | newest-mtime `/var/lib/ants/*/ledger.sqlite`, then `runs/*/ledger.sqlite` | `runs/default` |
| arm | `--arm` | `$ANTS_ARM` | `[run] arm` in `config.toml` | `arm` of the newest trial in the ledger | `portfolio` |

Verified: `cd empty-dir && ants top` prints the `no contract found` message and exits 1.

## Exit codes

| code | when |
|---|---|
| 0 | success (also `run` that stopped for any reason: budget, signal, pause) |
| 1 | `show`: reference not found; no contract found; other `SystemExit(str)` |
| 2 | `status`: hash chain broken; `doctor`: any `FAIL`; `data verify`: raw files differ; argparse usage error |
| 3 | `run`: another `ants run` already holds `<workdir>/ants.lock` (message names its pid) |

## Commands

| command | purpose | writes? |
|---|---|---|
| `run` | the campaign loop | ledger, `runs/<arm>/` |
| `status` | counts, disk level, chain head (JSON) | no |
| `top` | live dashboard / whole state as JSON | no |
| `best` | leaderboard per parameter cap | no |
| `show REF` | one trial: spec, stage metrics, lineage | no |
| `log` | recent ledger events | no |
| `diff A B` | the levers and per-stage metrics that differ between two trials | no |
| `workspace --out DIR` | the ledger as plain files (`trials.jsonl`, `frontier.json`, `failures.jsonl`, `trials/<id>/`, `SKILL.md`) for an agent or `grep` | `--out` dir |
| `doctor` | health check | no |
| `hint TEXT` | note for proposers | one `user_hint` event |
| `pause` / `resume` | create / delete `<workdir>/PAUSE` | flag file |
| `export` | CSV + JSONL dump | `--out` dir |
| `board` / `serve` | static HTML board / localhost server | `--out` dir |
| `janitor` | archive then safe cleanup | ledger, files |
| `shell` | interactive session; plain `ants` opens it once `ants onboard` has run | as its commands |
| `data` | catalog list / verify / build / fetch | `data/processed` (build), `data/raw` (fetch) |
| `onboard` | create `~/.ants`, stage the catalog, fetch datasets, write `config.toml` | `~/.ants`, `data/raw`, `data/processed` |
| `service install\|uninstall\|status\|logs` | keep `ants run` resident: systemd user unit (+ daily backup timer) or launchd agent | unit files |
| `backup` / `backups` / `restore` | verified online snapshot of the ledger / list / put one back | backup directory; `restore` replaces the ledger (old one moved aside) |
| `ui` / `home` | the terminal interface (plain `ants` on a terminal) / one screen printed and exit ([tui](tui.md)) | no |
| `update` | look for a newer ants (npm registry, or GitHub releases with `[update] source = "github"`); `--apply` installs it with the method that installed ants (npm, git pull, or pip) | install only with `--apply` |
| `sql` (alias `query`) | one read-only SELECT over the ledger, `--format table\|json\|jsonl\|csv`, `--schema` | no |

### run

```bash
ants run --contract contracts/tinyts_real.toml --workdir $PWD/runs/real --arm portfolio --seed 1 --max-evals 500
```

| flag | default | meaning |
|---|---|---|
| `--max-evals N` | none (runs forever) | stop when N ledger rows are in a final state (`done/failed/timeout/crashed`), **counting every stage** |
| `--max-cpu-s X` | none | stop when summed `cpu_s` of the arm reaches X |
| `--llm-env FILE` | `~/.config/ants/llm.env`, else `/etc/ants/llm.env` | endpoints for `llm*` / `portfolio_llm` arms; file must be mode 600 (`llm.load_endpoints` raises otherwise) |
| `--dream-every N` | 100 | summarise every N evaluations; only `llm_dream`, `portfolio_dream` |

Arms (`proposers.make_proposer`): `random`, `evo`, `evo_percap`, `qd`, `tpe` (needs `optuna`), `portfolio`, and the LLM arms
`llm`, `llm_dream`, `portfolio_llm`, `portfolio_dream`, `agent`, `portfolio_agent` (need a client). Unknown arm: `ValueError`. `agent` and `portfolio_agent` replace the one-shot LLM operator with one that reads the ledger through read-only tools before it proposes ([research note](research/meta-harness.md)); every tool call is a chained `agent_tool` event; LLM calls are capped per rolling day (`ANTS_LLM_MAX_CALLS_PER_DAY`, default 1500).

Behaviour: one `ants run` per workdir (`flock` on `<workdir>/ants.lock`, released by the kernel even after kill -9; a second one exits 3, verified).
SIGTERM / SIGINT set a stop flag: a running evaluation is abandoned within about 0.25 s (its process group is killed, the half-result is not recorded as evidence and the row is reclaimed on the next start), sleeps wake every 0.5 s. Stdout is the last step result.
Verified with a toy contract (30 evals, 2.1 s wall):

```
$ ants run --contract contracts/toy.toml --workdir $PWD/wd --arm random --max-evals 30
budget            # exit 0
```

Other last-step values: `idle`, `paused:user`, `paused:disk:pause`, `paused:disk:hardstop`, `breaker`, `ran:<id>:<state>`.
While paused or in breaker the loop sleeps 60 s (5 s for `idle` and `paused:user`) and re-checks.

### status

```
$ ants status --contract contracts/tinyts_real.toml --workdir runs/demo --arm portfolio
{"counts": {"done": 60, "queued": 1}, "free_gb": 118.3, "disk_level": "ok",
 "chain_ok": true, "chain_bad_seq": null, "head": "2de7693a5a93d50d", "unseals": 0}     # exit 0; 2 if chain_ok is false
```

`head` is the first 16 hex of the hash-chain head: the value to anchor off-box ([operations.md](operations.md)).

### top

`--every S` (default 5, seconds), `--once`. With `--json` it prints one object and exits 0.

```
ants tinyts_real · arm portfolio · epoch 9a60d4a9ac35 · chain ok 2de7693a5a93
disk 118.3 GB free (ok) · finished: 0 in 5 min, 34 in 1 h · last 3105s ago

stages  s0: 21 done   s1: 20 done   s2: 19 done, 1 queued

best per parameter cap
  ≤  300  0.8922 s2  (300 params, trial 16, via evo)  lookback=248, norm=last, augment=True
  ≤ 1000  0.8706 s2  (420 params, trial 8, via sweep)  norm=last
  ≤ 3000  0.8460 s2  (1190 params, trial 59, via evo)  lookback=352, period=22, norm=last, ens=3, ...
search mechanisms (discovery rate)  qd 1.00(3)  sweep 0.67(3)  tpe 0.33(3)  evo 0.25(8)
```

`--json` keys: `arm task epoch stages finished_5min finished_1h last_finish_age_s paused free_gb disk_level chain_tail_ok head
best_by_cap operators paths llm_calls_1h user_hints`. `chain_tail_ok` checks only the last 2000 events (`Ledger.verify_tail`).

**Which stage a value comes from.** Each row is ranked by the deepest stage the spec reached, then by its value there, and says which stage
(`s2` above). Values from different stages are different protocols (s0 is one dataset, one horizon, one fold) and are never ranked against each other.

### best

```
$ ants best ... --top 1 --cap 300 --json
{"300": [{"rank": 1, "value": 0.89221, "stage": "s2", "n_params": 300, "trial": 16, "key": "25e43b2c98f05e3c",
          "operator": "evo", "spec": "lookback=248, norm=last, augment=True"}]}
```

`--top N` (default 5) rows per cap; `--cap N` keeps one bin. Over-cap specs are excluded (`History.feasible`); `spec` lists non-default levers only.

### show

`REF` is a trial id (digits) or a `spec_key` prefix. Exit 1 if absent: text `no trial matches 'zzzz'`, JSON `{"not_found": ...}`.

```
trial 12  (done, arm portfolio, epoch 9a60d4a9ac35, proposed by evo:mutate)
non-default levers: lookback=248, norm=last, augment=True
stage   rel_mse   rel_mae   params   fit_s
  s0      0.9341    1.0336     300     0.1
  s1      0.8658    1.0018     300     5.7
  s2      0.8922    1.0206     300    11.2
secondary: horizon_decay=0.365, robustness_index=1.224, seasonal_fidelity=0.523
lineage (parent -> ... -> root):
  trial     4  value 0.9272  via sweep  edit None
```

`--json` keys: `trial spec_key arm epoch state source spec non_default stages error edit_from_parent lineage`.

### log

`-n N` (default 20), `--kind K`. Each row: `seq time kind` plus selected payload fields (`id stage state source path text
freeze_hash name to`, first three numeric `metrics`).

```
$ ants log -n 2 --kind trial_finished --json
[{"seq": 136, "time": "10-13 01:39:21", "kind": "trial_finished", "id": 53, "stage": "s0", "state": "done",
  "source": "tpe", "metrics": {"bytes": 1508, "fit_s": 0.0705, "n_params": 377}}, ...]
```

Event kinds seen in code: `step_error epoch_started trial_finished trial_requeued trial_reclaimed trial_crashed recovered artifact_added
artifact_archived archive_mismatch artifact_deleted artifact_changed candidate_frozen sealed_evaluation sealed_result user_hint model_registered model_status`.

### doctor

Seven checks (levels `OK`/`WARN`/`FAIL`); exit 2 on any `FAIL`.

| check | WARN | FAIL |
|---|---|---|
| hash chain (full if < 300 000 events, else last 5000) | | broken |
| disk free vs contract thresholds | level `warn` | level `clean`, `pause`, `hardstop` |
| freshness of last finished trial | `PAUSE` flag present, or no trial yet | > 30 min since a finish while trials are queued/running |
| failure rate, last 200 finals | >= 10% | >= 40% |
| trials `running` past their lease | any | |
| artifacts not yet archived | >= 5000 | |
| LLM parse rate, 24 h | <= 60% | |
| epochs recorded | more than 1 | |

Real output on the demo copy (the run was stopped with one trial still queued, so the freshness check fails):

```
[  OK] hash chain intact
[  OK] disk free 118.2 GB (level ok)
[FAIL] no trial finished for 52 min while work is queued (stuck?)       # exit 2
[  OK] failure rate over the last 60 trials: 0%
```

### hint, pause, resume

`hint "text"` stores the text (whitespace collapsed to one line, control characters stripped, at most 500 characters) as a `user_hint` event (LLM prompts include the latest three; `top` shows them). It cannot
change a gate or budget (`tests/test_ui.py::test_hint_reaches_the_llm_prompt_but_only_as_a_suggestion`).
`pause` writes `<workdir>/PAUSE`; `Campaign.step` then returns `paused:user` and starts nothing new; `resume` deletes it.

### export

`--out DIR` (required). Writes `trials.csv` (cells that start with `= + - @` get a leading `'` so spreadsheets do not run them; columns `id arm stage state source spec_key seed attempt cpu_s error`, then
`p_<param>` for every space parameter, then `m_<primary>` and `m_<cap_metric>`) and `events.jsonl` (every event with `prev_hash`, `hash`).
Prints `wrote DIR/trials.csv and events.jsonl (chain head 77196bdf1e67d2e3)`.

### board, serve

`board --out DIR [--arms a,b]` writes `board.html` and `board.json` (keys `generated task primary direction chain_ok chain_head arms judges`).
`serve` takes the same plus `--port` (default 8765) and `--every S` (default 60) and rebuilds periodically. It binds `127.0.0.1` only.
Verified: `GET /board.html` and `/board.json` returned 200 on port 8799; `lsof` showed the listener on `127.0.0.1:8799`.

### janitor

```bash
ants janitor --contract ... --workdir ... --archive-dir /var/lib/ants/archive [--dry-run] [--force-bytes N]
```

1. With `--archive-dir`: copy un-archived artifacts to the directory backend and verify by re-reading (`archive.archive_pending`, 200 per call).
2. Compute `level`; if not `ok`, free `(warn_gb - free_gb)` bytes; `--force-bytes N` frees N even when the level is `ok`.
3. Delete only artifacts that pass every rule in `janitor.plan_cleanup`; print `level=<l> {deleted, skipped, freed, dry_run}`.

Verified: `ants janitor --dry-run` on the demo prints `level=ok {'deleted': 0, 'skipped': 0, 'freed': 0, 'dry_run': True}`.
`S3Archive` exists in code but no CLI flag selects it (`stub`, [surfaces.md](surfaces.md)).

### shell

`ants shell` (or plain `ants`). Lines are the subcommands `top best show log hint pause resume doctor status export board` with
contract, workdir and arm pre-filled; `hint` takes free text; `export` / `board` default `--out` to `<workdir>/export` / `board`.
`help`, `quit`, `exit`, `q`. Unknown input prints a list and the session continues (tested). Verified with piped input:

```
antslab> cap <= 1000
  1. 0.9272  420 params  trial 4  sweep  norm=last
antslab> unknown command 'bogus'; try: top, best, show, log, hint, pause, resume, doctor, status, export, board
```

### onboard, service, backup, restore, sql

`ants onboard [--tier dev|standard|extended|all] [--no-fetch] [--contract NAME] [--workdir DIR] [--arm ARM]`: creates `~/.ants` (`$ANTS_HOME`), copies the catalog, `sources.toml` and
the pinned `MANIFEST.sha256` of this version into `~/.ants/data`, downloads the datasets of the tier (https only, resumable, each file hashed and compared with the pinned manifest; a
file that differs is set aside as `*.rejected`), builds the processed files for what was downloaded and writes `config.toml`. Safe to repeat. Plain `ants` before this prints one line and
downloads nothing; on a terminal it asks first.

`ants service install [--name N] [--now] [--print]`: Linux writes `~/.config/systemd/user/ants-N.service` (`Type=notify`, `WatchdogSec=300`, `Restart=always`, exit 3 not restarted)
and a daily `ants-N-backup.timer`; macOS writes `~/Library/LaunchAgents/io.ptoq.ants-N.plist` (`KeepAlive` after failure, 30 s throttle). `--print` shows the files and writes nothing.
After `--now` on Linux run `loginctl enable-linger $USER` so it survives logout. The loop sends the watchdog heartbeat itself, so a hung loop is restarted and a long evaluation is not.

`ants backup [--dest D] [--keep 14] [--gzip]` snapshots with SQLite's online backup API, keeps it only if SQLite's integrity check and the event-chain check pass, writes a sidecar
(sha256, event count, chain head, schema version) and appends the head to `anchors.jsonl` (never pruned). `ants restore [NAME]` verifies the snapshot first, refuses while a daemon holds the
workdir, and moves the old ledger aside instead of deleting it. Default destination `~/.ants/backups/<workdir name>` (`$ANTS_BACKUP_DIR`, `[backup] dest`).

`ants sql "SELECT ..."` opens the ledger read-only with an authorizer that allows nothing but SELECT, a 10 s time limit and a 1000-row cap. `--format csv` neutralises formula cells.

`ants update` caches the answer for a day in `~/.ants/update.json`. Interactive commands (`shell`, `top`, `best`, `status`) print one line on stderr when the cache knows a newer version; nothing is printed in scripts (no terminal), with `ANTS_NO_UPDATE_CHECK=1`, or with `[update] check = false`. `ants run` never checks and never updates itself: a resident daemon must not change under itself, so update, then `ants service restart`. `ants doctor` reads the cache only.

### data

No `--contract`. Flags: `--catalog` (default: found by `locate_catalog`), `--names a,b`, `--all`, `--partial`, `--tier` (fetch).

| action | what | verified output |
|---|---|---|
| `data list` | datasets per role; `--all` includes `syn_*` | `dev 4`, `confirm 21`, `sealed 10`, `stress 3`, `synthetic entries: 95` |
| `data verify` | SHA-256 of raw files against `data/MANIFEST.sha256`; `--partial` checks only files present | `raw files intact`, exit 0 |
| `data build` | write `data/processed/<name>.npz`; `--names` limits | Not run: rewrites `data/processed` in the repository |
| `data fetch` | download the files of `--tier` listed in `data/sources.toml` into `raw/<id>/`; resumable, verified against the pinned manifest | dev tier fetched into an empty `ANTS_HOME`: 5 files, all matched, `data verify --partial` intact |

## Environment variables

| variable | read by | effect |
|---|---|---|
| `ANTS_CONTRACT`, `ANTS_WORKDIR`, `ANTS_ARM` | `cli._resolve` | flag-less defaults |
| `ANTS_BACKUP_DIR` | `system.backup_dir` | where `ants backup` writes and `ants doctor` looks |
| `ANTS_CATALOG`, `ANTS_HOME` | `data.locate_catalog` | where `data/catalog.toml` is; `ANTS_HOME` is also the launcher's venv root (`bin/ants.js`) |
| `ANTS_MAX_WINDOW_ELEMS` | `gyms/tinyts.py` | cap on a window matrix, default 4 000 000 float64 elements. **Changes memory and results; set it only deliberately** |
| `ANTS_PYTHON` | `bin/ants.js` | interpreter for the `npx` launcher |
| `LLM_<TAG>_BASE/KEY/MODEL` | `llm.load_endpoints` (from the env file, not the process environment) | one endpoint per TAG |
| `TOY_FAULT`, `TOY_FAULT_MOD`, `TOY_SLEEP` | `gyms/toy.py` | fault injection for tests |

The runner starts each evaluation with a **clean environment** (`PATH`, `HOME=<trial dir>`, `PYTHONHASHSEED=0`, one-thread BLAS
variables, `PYTHONPATH`, plus the names in the contract's `env_passthrough`). `ANTS_*` variables do not reach the gym unless listed there.

## Known issues found while documenting

| symptom | cause | status |
|---|---|---|
| `top` / `best` numbers differ from `show` | the leaderboard uses the s0 value, see `top` above | open: read `show`, or `export` and filter `stage == s2` |
| `run` with a relative `--workdir` failed every trial (`spec.json` not found from the trial directory) | paths were not made absolute | **fixed in the current tree**: reproduced earlier the same day with 3 `failed` rows and an open breaker; after the change `ants run --workdir rel2 --max-evals 8` printed `budget`, exit 0 |
