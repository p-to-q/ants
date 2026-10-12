# Contract reference

A contract is the TOML file that fixes a gym's typed space, parameter caps, stages, budgets and commands. Source:
`src/antslab/contract.py` (`load_contract`, `Param`, `Space`). Status: `stable`; load-time validation is covered by
`tests/test_security_contract.py`. Real examples: `contracts/tinyts_real.toml`, `tinyts_synth_hard.toml`, `tinyts_synth_clean.toml`.

Loading fails closed with `SpecError: contract <section>.<key>: <reason>`. The file must be at most 1 MiB. Every rule below was
exercised against `load_contract` while writing this page (rejections quoted from real messages). Not every error is a
`SpecError`: a missing `[task]`, `[budget]`, `[objective]` or `[commands]` table is one (`missing or not a table`); a bad TOML syntax is one.

## Sections

| section | required | purpose |
|---|---|---|
| `[task]` | yes | identity, evaluator source files, environment pass-through |
| `[budget]` | yes (may be empty) | timeouts, attempts, sealed-look limits |
| `[objective]` | yes | metric, direction, caps, static filters |
| `[cascade]` | no | stages and promotion |
| `[record]` | no | which stages keep files |
| `[disk]` | no | free-space levels |
| `[commands]` | yes | argv templates for the gym |
| `[space.<name>]` | no (but a run needs at least one) | one table per parameter |
| `[sealed]` | no | `datasets = "catalog:sealed"` or a list; read by `gate.py`, not validated at load |

### [task]

| key | type | default | rule |
|---|---|---|---|
| `name` | string | required | plain text, 1-100 chars. Shown in `top`; part of the contract hash |
| `gym` | string | required | `[A-Za-z0-9_]`, 1-64 chars. Ledger key: trials are filtered by `(gym, arm)` |
| `description` | string | none | not read by code (kept in the file for humans; still part of the contract hash) |
| `code_paths` | list of strings | `[]` | paths relative to **the contract file**; a directory means every `*.py` below it. Hashed into the epoch and into every freeze. Max 64. `contracts/tinyts_real.toml` lists the gym, `metrics.py`, `data.py` and both catalog files, so a change to a dataset role or split is a new epoch |
| `env_passthrough` | list of names | `[]` | upper-case names copied from the supervisor's environment into the gym process. Refused: `PATH PYTHONPATH HOME IFS ...`, anything starting `LD_`, `DYLD_`, `PYTHON`, or containing `KEY TOKEN SECRET PASSW CREDENTIAL AUTH` |
| `python` | string | `"python3"` | loaded but **not used**: `{python}` is always the supervisor's `sys.executable` (`campaign.py`) |

### [budget]

| key | default | range | meaning |
|---|---|---|---|
| `trial_timeout_s` | 120 | 1 .. 604800 | wall time for a stage with no entry in `stage_timeouts`; the sealed run gets 4x |
| `stage_timeouts` | `{}` | keys must be declared stages | per-stage wall time, e.g. `{ s0 = 60, s1 = 600, s2 = 1800 }` |
| `max_attempts` | 2 | 1 .. 20 | a child killed by a signal is requeued while `attempt < max_attempts`; a lost runner after that is `crashed` |
| `unseal_limit` | 1 | 0 .. 100 | sealed looks per (gym, epoch, sealed set) |
| `unseal_lifetime` | `3 x unseal_limit` | read raw | sealed looks per (gym, sealed set) over all epochs |

The claim lease is `max(stage_timeouts, trial_timeout_s) + 60` seconds. A timeout kills the whole process group (SIGTERM, 3 s, SIGKILL).
The runner also sets `RLIMIT_CPU` to `2 x timeout + 5` s and `RLIMIT_FSIZE` to 256 MiB for the child.

### [objective]

| key | default | rule |
|---|---|---|
| `primary` | required | name of the metric in `metrics.json` that ranks specs |
| `direction` | `"min"` | `"min"` or `"max"`; anything else is rejected |
| `cap_metric` | none | metric holding the cost (e.g. `n_params`). Must be given **together with** `caps` |
| `caps` | `[]` | positive, distinct, increasing numbers, at most 64. They are the bins of the frontier and `max(caps)` is the hard limit |
| `static_cost` | none | `antslab.<module>:<function>`; `callable(spec) -> number`, free analytic cost. Other module prefixes are rejected |
| `static_mem` | none | same form; estimated peak MB of one evaluation |
| `mem_cap_mb` | 800 | specs with `static_mem(spec) > mem_cap_mb` are dropped, never kept as stepping stones |
| `stone_quota` | 0.05 | 0..1; probability of keeping a spec whose `static_cost > max(caps)` |

How the cap behaves (`campaign._refill`, `_should_promote`, `history.feasible`):

| step | rule |
|---|---|
| proposal | `static_mem` over `mem_cap_mb`: dropped. `static_cost > max(caps)`: dropped with probability `1 - stone_quota`, else enqueued as a **stepping stone** |
| evaluation | runs normally; `metrics.json` must contain finite numbers for `primary` and `cap_metric` (`runner.validate_metrics`, else the trial is `failed`) |
| promotion | never, if `metrics[cap_metric] > max(caps)` |
| leaderboard / frontier | over-cap rows are excluded; each feasible row goes to the smallest cap bin that holds it |

### [cascade]

| key | default | rule |
|---|---|---|
| `stages` | `["s0"]` | 1-8 distinct names `[A-Za-z0-9_]`, ordered cheap to expensive. Passed to the gym as `{stage}` |
| `promote_quantile` | 0.5 | in (0, 1] |
| `min_history` | 8 | integer >= 0 |

Promotion after a `done` trial at a non-final stage (`campaign._should_promote`), with `n` = feasible results of that stage in the current epoch:

| `n` | promoted if |
|---|---|
| `< max(3, min_history // 3)` | always (warm-up) |
| `< min_history` | value is in the better half of the stage |
| otherwise | value is within the best `promote_quantile` of the stage |

The last stage never promotes. Demo ledger: 21 s0, 20 s1, 19 s2 rows, because every `n` was in warm-up.

### [record]

`full_stages` (default: the last stage). Trials at these stages keep their directory, `spec.json` and `metrics.json` (registered as
**protected** artifacts, never deleted by the janitor) and gzipped logs. Cheap stages keep the ledger row only; their directory is removed.
Failed, timed-out and crashed trials always keep logs.

### [disk]

| key | default |
|---|---|
| `warn_gb` | 15 |
| `clean_gb` | 12 |
| `pause_gb` | 8 |
| `hardstop_gb` | 6 |

Must satisfy `warn > clean > pause > hardstop > 0`. Levels are `ok / warn / clean / pause / hardstop` by free space of the workdir's filesystem.
`pause` and `hardstop` both make `Campaign.step` return `paused:disk:<level>` ([operations.md](operations.md)).

### [commands]

Only `eval` (required) and `final` (used by `gate.seal`) are allowed. Each is an **argv list**, 1-64 strings, no shell.

| placeholder | value |
|---|---|
| `{python}` | the supervisor's `sys.executable` |
| `{stage}` | stage name; `final` for the sealed run |
| `{spec}` | path of `spec.json` in the trial directory (absolute only if `--workdir` is absolute) |
| `{out}` | trial directory; the gym's cwd; it must write `metrics.json` here |
| `{seed}` | integer: `contract seed * 1_000_003 + proposal counter` for `eval`; the `seal` seed (default 12345) for `final` |
| `{attempt}` | 1, 2, ...; the fencing token of this claim (`final` always 1) |

Any other placeholder, attribute access (`{python.__class__}`), conversion or format spec is rejected at load.

## [space.<name>]

Parameter names: `[A-Za-z_][A-Za-z0-9_]{0,63}`, at most 128 parameters. The space is closed: a spec is a flat object with
**exactly** these keys (extra and missing keys are rejected by `Space.validate`).

| key | applies to | default | rule |
|---|---|---|---|
| `type` | all | required | `int`, `float`, `logfloat`, `cat`, `bool` |
| `low`, `high` | int, float, logfloat | required | `low < high`, finite; `logfloat` needs `low > 0` |
| `choices` | cat | required | 1-256 distinct scalars (`1`, `1.0`, `true` count as the same value) |
| `default` | all | see below | must itself validate |
| `when` | all | none | `{other_param = [allowed values]}`; the parameter is active only if **every** listed parameter holds an allowed value. Must name another declared parameter; the list must be non-empty |
| `layer` | all | `""` | free label (`data`, `architecture`, `optim`, `post` ...), at most 64 chars; used to group levers |
| `doc` | all | `""` | at most 500 chars; shown to LLM proposers by `Space.describe` |
| `role` | cat, bool | `""` | only `"skeleton"` is allowed, and only on discrete parameters |

Unknown keys are rejected. When `default` is omitted: `cat` -> first choice, `bool` -> `false`, `logfloat` -> geometric mean of the bounds, `int`/`float` -> midpoint (int rounded).

Validation (`Param.validate`, strict, never clamps), verified on `contracts/tinyts_real.toml`:

| input | result |
|---|---|
| `lookback = 47` (range 48..720) | `SpecError: lookback: 47 outside [48, 720]` |
| `lookback = 100.5` | `SpecError: lookback: expected integer` |
| `lookback = 100.0` | accepted, stored as `int` 100 |
| `clip = 1` (bool) | `SpecError: clip: expected bool` |
| NaN / inf | `non-finite` |

Dormant parameters: a spec still carries every key, but `Space.key(spec)` hashes only active ones, so specs that differ only in a dormant
lever are the same candidate (`tinyts_real.toml`: 25 parameters, 16 active at the defaults). `role = "skeleton"` marks discrete structure: a skeleton
combination is a **niche** (`Space.niche`), swept exhaustively by `SweepProposer` and never mutated by DE/CEM. The product of
skeleton sizes is limited to 100 000 cells. `tinyts_real.toml` has 4 skeleton parameters (`norm core decomp pretrain`).

## Identity: what changes the epoch

`Campaign._epoch` hashes **evaluator code** (`code_paths`), **the whole parsed contract** (`json.dumps(raw, sort_keys)`; comments and key order do not count, but editing
`description` does) and the **data manifest**. Python, numpy and platform are recorded but not hashed. Trials of different epochs are never mixed
by `History` (`tests/test_campaign.py::test_history_ignores_other_epochs`).

## Minimal example (toy gym)

Save as `contracts/toy.toml` in a tree that contains `src/antslab/gyms/toy.py`. Verified: `load_contract` returns caps `[100, 300]`, stages
`s0 s1 s2`, thresholds `15/12/8/6`, `mem_cap_mb 800`, `stone_quota 0.05`, defaults `{a: 0.5, b: 16, c: 'u'}`, and `ants run ... --arm random --max-evals 30`
finished in 2.1 s with `budget` and `ants top` showing a frontier at both caps.

```toml
[task]
name       = "toy"
gym        = "toy"
code_paths = ["../src/antslab/gyms/toy.py"]   # relative to THIS file; hashed into the epoch

[budget]
trial_timeout_s = 20
max_attempts    = 2
unseal_limit    = 1

[objective]
primary    = "val_mse"
direction  = "min"
cap_metric = "n_params"
caps       = [100, 300]

[cascade]
stages           = ["s0", "s1", "s2"]
promote_quantile = 0.5
min_history      = 4

[commands]
eval  = ["{python}", "-m", "antslab.gyms.toy", "--stage", "{stage}", "--spec", "{spec}", "--out", "{out}", "--seed", "{seed}", "--attempt", "{attempt}"]
final = ["{python}", "-m", "antslab.gyms.toy", "--final", "--stage", "final", "--spec", "{spec}", "--out", "{out}", "--seed", "{seed}"]

[space.a]
type = "float"
low  = 0.0
high = 1.0

[space.b]
type = "int"
low  = 1
high = 30

[space.c]
type    = "cat"
choices = ["u", "v", "w"]
```

```bash
PYTHONPATH=src .venv/bin/ants run --contract contracts/toy.toml --workdir "$PWD/wd" --arm random --max-evals 30
```

## Differences between the three shipped contracts

| | `tinyts_real` | `tinyts_synth_hard` | `tinyts_synth_clean` |
|---|---|---|---|
| data | catalog, real dev sets (`--profile catalog`) | synthetic, spikes and shifts (`--profile hard`) | synthetic, plain |
| primary | `rel_mse` | `rel_mse` | `val_mse` |
| `stage_timeouts` | 60 / 600 / 1800 | 30 / 150 / 900 | none (120 each) |
| `mem_cap_mb` | 750 | 800 | no `static_*` filters |
| `pretrain` choices | `none`, `pool` | `none`, `synth` | not in space |
| `min_history`, `promote_quantile` | 15, 0.25 | 15, 0.25 | 20, 0.25 |
| space | 25 parameters | 25 | 13, no `default`/`layer`/`when` |

`tinyts_synth_clean` is the oldest form and shows that `layer`, `doc`, `default`, `when` and `role` are optional.

## Known gaps

| gap | where |
|---|---|
| `task.python` is validated but ignored | `contract.py` vs `campaign.py:38` |
| `static_cost` / `static_mem` must live in the `antslab` package, so an external gym cannot supply a free pre-filter without patching the package; the cap is then enforced only at promotion and in the leaderboard | `_REF` in `contract.py` |
| `[sealed]` and `budget.unseal_lifetime` are read from the raw dict and not validated at load | `gate.py` |
| `docs/modules/contract.md` says only "A bad contract fails at load time" | thinner than the code; this page is the reference |
