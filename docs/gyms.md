# Writing a gym

A gym is the fixed evaluator for one task domain. The core knows nothing about forecasting: a new domain is a gym plus a
[contract](contracts.md). Status: the interface is `stable` (`tests/test_runner.py`, `tests/test_campaign.py`); the forecasting gym is `experimental`.
Everything on this page marked "verified" was run; the worked example at the end was executed under the campaign in a temporary directory.

## The interface

| | rule | enforced by |
|---|---|---|
| process | a command from `[commands].eval`, run as a subprocess in its own process group, cwd = the trial directory `{out}` | `runner.run_command` |
| input | `--spec` points to `spec.json`: a flat object with **every** parameter of the contract (dormant ones included), validated before the gym sees it | `Space.validate`, `campaign._execute` |
| output | `metrics.json` in `{out}`: a regular file (no symlink) of at most 1 MiB and 512 keys, a flat JSON object; `primary` and `cap_metric` must be finite numbers (not bool, not string). A `NaN`/`Infinity` literal **anywhere** in the file rejects it. A stale `metrics.json` is deleted before the run | `runner._read_metrics`, `validate_metrics` |
| exit | 0 on success. Any other exit code is a `failed` trial and the last 400 bytes of stderr become the error. Death by SIGXFSZ / SIGXCPU (the runner's own limits) is a plain failure, not retried | `runner.run_command` |
| stdout / stderr | go to files in `{out}`, gzipped afterwards; never into the supervisor's memory | `runner`, `archive.compress_logs` |
| arguments | `--stage --spec --out --seed --attempt` always; `--final` for the sealed run | `[commands]` placeholders |
| environment | `PATH`, `HOME={out}`, `PYTHONHASHSEED=0`, one-thread BLAS variables, `PYTHONPATH` of the supervisor, plus `env_passthrough` names | `runner.py` |

The `evaluate(spec, stage)` of this repository's gyms is the function behind this process: it maps a spec and a stage name to a dict of numbers.

What the runner does with bad output (all verified with `run_command`):

| gym behaviour | recorded error |
|---|---|
| `rel_mse` is NaN | `bad metrics.json: ValueError: non-finite number NaN` |
| `n_params` absent | `metric 'n_params' missing or non-finite` |
| value is the string `"0.9"` | `metric 'rel_mse' is not a number` |
| no `metrics.json` | `no metrics.json` |
| `raise SystemExit(3)` | `exit 3: <stderr tail>` |
| sleeps past the stage timeout | `timeout after 2s` (whole process group killed) |
| killed by a signal | requeued while `attempt < max_attempts`, then `crashed` |

A NaN in a **secondary** key rejects the whole file too (verified), so `gyms/tinyts.py` drops NaN values itself before writing. Descendants of the gym that leave its process group are found by the per-trial token `ANTS_TRIAL_TOKEN` in their environment and killed; core dumps are disabled.

## Determinism, seeds, attempts

| topic | rule | example |
|---|---|---|
| same spec + stage -> same metrics | required: promotion compares values across trials and the replay pack assumes it | `tests/test_tinyts.py::test_deterministic` |
| `--seed` | different for every trial (`seed * 1_000_003 + counter`). Use it only for noise you want to vary, never for the **data split** | `toy.py` seeds its noise from it; `tinyts.py` accepts and ignores it |
| data seeds | fixed in the gym, per stage | `STAGES`, `CAT_STAGES` in `tinyts.py` |
| `--attempt` | the fencing token of this claim: 1, then 2 after a requeue. A gym may ignore it. If the process is killed, the retry runs with `attempt=2`; a stale process cannot overwrite the retry because `Ledger.finish` matches on `(id, attempt)` | `toy.py` kills itself only on attempt 1 |
| threads | set to 1 by the runner so two cores are not oversubscribed | `runner.py` |
| hash seeds | `PYTHONHASHSEED=0`: do not rely on set/dict-of-str ordering across runs | `runner.py` |

## Budgets

| budget | where set | default | note |
|---|---|---|---|
| wall time per stage | `[budget] stage_timeouts`, else `trial_timeout_s` | 120 s | process group is killed; the trial is `timeout` |
| CPU time | runner `RLIMIT_CPU` | `2 x timeout + 5` s | second line of defence |
| file size | runner `RLIMIT_FSIZE` | 256 MiB | per file written by the gym |
| memory | not limited by the runner; the systemd cgroup does it (`MemoryMax=650M` per instance) | | declare a `static_mem` estimate so a spec the box cannot afford is dropped before it runs |
| cost cap | `[objective] caps` + `cap_metric` | | a constraint, never an objective term |

`tinyts.py` treats evaluation memory as part of the cost model: `est_mem_mb(spec)` is an additive estimate, and
`tests/test_tinyts.py::test_memory_estimate_never_badly_under_predicts_measured_peaks` re-measures peak RSS in a fresh interpreter.
Measured here on macOS arm64 with the default `tinyts_real` spec (real catalog profile, one process each): s0 179 MiB, s1 672 MiB, s2 745 MiB peak RSS,
against `est_mem_mb = 560`. That is 33% over the estimate at s2. macOS and Linux report RSS differently, so this is a warning, not a conclusion.
Not measured on the target machine.

## Cheap stage versus full-record stage

| | cheap stages (e.g. `s0`) | full-record stages (`[record] full_stages`) |
|---|---|---|
| purpose | rank most candidates at low cost | decision-grade numbers |
| files kept | none (the directory is deleted); the ledger row has all metrics | `spec.json`, `metrics.json` (protected), gzipped logs |
| `tinyts_real` | s0: 1 dataset (etth1), horizon 96, 1 fold; 0.2 s | s2: 4 datasets, 4 horizons, 3 folds (48 tasks); 10.9 s |
| failure | logs kept | logs kept |

Measured on the default spec (macOS arm64, one process, `nice -n 15`): s0 0.20 s, s1 4.95 s (36 tasks), s2 10.87 s (48 tasks).
The proxy question (does s0 rank like s2?) is a qualification test you should run before a long campaign; it is listed in `docs/evaluation/E1-quality.md` and `docs/open.md`; the cascade does not check it for you.

## Data roles (a gym that reads data)

Read [DATA.md](DATA.md). The rules a new gym must follow:

| rule | how `tinyts.py` does it |
|---|---|
| the search uses `dev` data on validation windows only | `CAT_STAGES` names `etth1 ettm1 weather exchange`; no agent chooses data |
| standardise with train statistics only; never clip targets | `Series.standardised()`; clipping applies to inputs only |
| training targets end before the evaluation block starts | `rolling_blocks`, `tests/test_real_eval.py` |
| pre-training may use other dev families only | `_pool_prior` skips the target's own family |
| test windows are never read by validation | `test_test_split_is_not_touched_by_val_stages` |
| `--final` reads test windows of `dev`, `confirm` and `sealed` sets | only `gate.seal` should call it: it spends a look first and exports `ANTS_ALLOWED_ROLES=dev,confirm,sealed` |
| role checks at load time | `Catalog.load` refuses datasets outside `ANTS_ALLOWED_ROLES` (verified: `=confirm` gives `RoleError: dataset 'etth1' (access role dev) is not allowed`) |

Gap: the campaign does not set `ANTS_ALLOWED_ROLES` for search stages (`campaign._execute` passes only `env_passthrough`). Listing it there
(`env_passthrough = ["ANTS_ALLOWED_ROLES"]`, and `export ANTS_ALLOWED_ROLES=dev` in the service) should make the search fail closed. Verified for the gym alone, Not run through a campaign.

## Negative controls

A gym is not qualified until models show **no skill** where there is none. `tests/test_synth_suite.py::test_negative_controls_show_no_skill_through_the_real_pipeline`
runs white noise and a random walk, and `test_shuffled_real_data_is_a_negative_control_through_the_real_pipeline` runs time-shuffled real series. Another test checks that no
model beats the oracle noise floor of the SNR ladder. For a new domain, add (1) a label-shuffled or noise-only dataset, (2) a case with a known best answer, and (3) an
over-cap model that must not become a leader.

## How the two shipped gyms do it

| | `gyms/toy.py` (stdlib, 40 lines) | `gyms/tinyts.py` (numpy, 664 lines) |
|---|---|---|
| purpose | CI and fault injection | ultra-light forecasters trained in closed form |
| metrics | `val_mse`, `n_params` | `rel_mse`, `rel_mae`, `val_mse`, `n_params`, `bytes`, `n_tasks`, `fit_s`; s2 adds `robustness_index seasonal_fidelity horizon_decay` |
| noise | gaussian, std 0.30 / 0.08 / 0.02 for s0 / s1 / s2, seeded by `--seed` | none; closed-form solve on fixed windows |
| faults | `TOY_FAULT=kill|hang|nan|exit`, `TOY_FAULT_MOD=N`: fires when `seed % N == 0`; `kill` only on attempt 1 | none |
| static cost | none | `count_params` (analytic, at horizon 720) and `est_mem_mb`, wired in the contract |
| profiles | one | `clean`, `hard` (synthetic), `catalog` (real data via `data/catalog.toml`) |

## Worked example: a 40-line gym

Verified in a temporary directory outside the repository: `ants run --arm portfolio --max-evals 120` finished in 9.7 s and 14.9 s in two runs (busy machine) with the same output `budget`,
`ants doctor` all `OK`, and the best row per cap came from `bins=2` (cap 4), `bins=6` (cap 16) and `bins=25` (cap 64).
`evaluate` is called twice with the same spec and returns equal dicts.

`mygym/quad.py` (a histogram regressor on a noisy sine; `rel_mse` is the ratio to the global-mean predictor; fidelity is the number of validation points):

```python
"""Worked example gym: histogram regressor on a noisy sine. Stdlib only."""
import argparse, json, math, random
from pathlib import Path

STAGES = {"s0": 60, "s1": 400, "s2": 4000}          # fidelity = number of validation points
DATA_SEED = {"train": 11, "val": 12, "test": 13}     # fixed: the same candidate always sees the same data

def count_params(spec):
    return int(spec["bins"])

def series(split, n):
    r = random.Random(DATA_SEED[split])
    xs = [r.random() for _ in range(n)]
    return xs, [math.sin(6 * math.pi * x) + r.gauss(0, 0.3) for x in xs]

def evaluate(spec, stage, split="val"):
    k = spec["bins"]
    xt, yt = series("train", 2000)
    sums, cnt = [0.0] * k, [0] * k
    for x, y in zip(xt, yt):
        i = min(int(x * k), k - 1); sums[i] += y; cnt[i] += 1
    mu = sum(yt) / len(yt)
    w = [(1 - spec["shrink"]) * (s / c if c else mu) + spec["shrink"] * mu for s, c in zip(sums, cnt)]
    xv, yv = series(split, STAGES[stage] if split == "val" else 4000)
    mse = sum((w[min(int(x * k), k - 1)] - y) ** 2 for x, y in zip(xv, yv)) / len(xv)
    ref = sum((mu - y) ** 2 for y in yv) / len(yv)   # reference: the global mean
    return {"rel_mse": mse / ref, "n_params": count_params(spec)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True); ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--attempt", type=int, default=1); ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())
    m = evaluate(spec, "s2", "test") if a.final else evaluate(spec, a.stage)
    Path(a.out, "metrics.json").write_text(json.dumps(m))

if __name__ == "__main__":
    main()
```

`contracts/quad.toml` (next to the `mygym/` directory; `code_paths` is relative to the contract file):

```toml
[task]
name = "quad"
gym = "quad"
code_paths = ["../mygym"]

[budget]
trial_timeout_s = 30
stage_timeouts = { s0 = 10, s1 = 20, s2 = 30 }

[objective]
primary = "rel_mse"
direction = "min"
cap_metric = "n_params"
caps = [4, 16, 64]

[cascade]
stages = ["s0", "s1", "s2"]
promote_quantile = 0.5
min_history = 6

[record]
full_stages = ["s2"]

[commands]
eval = ["{python}", "-m", "mygym.quad", "--stage", "{stage}", "--spec", "{spec}", "--out", "{out}", "--seed", "{seed}", "--attempt", "{attempt}"]
final = ["{python}", "-m", "mygym.quad", "--final", "--stage", "final", "--spec", "{spec}", "--out", "{out}", "--seed", "{seed}"]

[space.bins]
type = "int"
low = 1
high = 128
default = 8

[space.shrink]
type = "float"
low = 0.0
high = 0.9
default = 0.0
```

```bash
touch mygym/__init__.py
export PYTHONPATH="$PWD:/path/to/ants/src"          # the child inherits PYTHONPATH; the module must be importable
ants run --contract contracts/quad.toml --workdir "$PWD/wd" --arm portfolio --seed 1 --max-evals 120
ants best --contract contracts/quad.toml --workdir "$PWD/wd" --arm portfolio --top 1
```

```
cap <= 4
  1. 0.8883  2 params  trial 32  qd  bins=2, shrink=0.5022
cap <= 16
  1. 0.4403  6 params  trial 69  evo  bins=6, shrink=0.1792
cap <= 64
  1. 0.1889  25 params  trial 94  evo  bins=25
```

Two things this example taught while it was being verified:

| finding | consequence |
|---|---|
| `static_cost = "mygym.quad:count_params"` is rejected: `must look like antslab.module:function` | an external gym has no free pre-filter; over-cap specs are evaluated, then blocked at promotion. Put the function in the `antslab` package to get the filter |
| `role = "skeleton"` on an `int` is rejected: `a skeleton parameter must be discrete (cat or bool)` | niches come from `cat`/`bool` parameters only |

## Checklist before a long run

| check | how |
|---|---|
| same spec twice gives equal metrics | call `evaluate` twice, as above |
| a NaN, a missing key and a hang each end as `failed` / `timeout`, not as a result | `run_command` cases above |
| cheap stage correlates with the full stage | rank correlation of s0 vs s2 on 50+ specs; Not automated |
| a no-skill dataset gets `rel_mse >= 1` | negative control |
| an over-cap spec never leads | `ants best` lists only feasible rows |
| the contract loads | `load_contract` ([contracts.md](contracts.md)) |
