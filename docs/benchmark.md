# TinyTS-Bench v0: pre-registered protocol

Status: **DRAFT, not frozen, nothing run.** This page fixes, before any benchmark run, what is compared, how it is scored and what each outcome means.
Freezing = a commit that tags this file and an entry in [changes.md](changes.md). After that, any change is a dated amendment in section 12, never an edit of the text.
Every RESULTS cell says `pending`. No number in sections 1 to 11 is a result; the few figures quoted are measurements of the tool chain, with their source.
Related: [open.md](open.md) questions 1 to 3, [DATA.md](DATA.md), [contracts.md](contracts.md), [operations.md](operations.md). Existing reference points: `assets/frontier.json`, `assets/make_figures.py`.

## 1. Question and hypotheses

How much of an unconstrained model's skill survives when the parameter budget is a hard wall, and does the ants portfolio find it more efficiently than simpler searchers?

| id | hypothesis | primary comparison |
|---|---|---|
| H1 | the ants portfolio at budget B reaches a lower dev-validation `rel_mse` than uniform random search at budget 2B, same cascade, same cap | ants vs random (2B) |
| H2 | the portfolio beats TPE and per-cap evolution at the same budget B | ants vs TPE, ants vs evo_percap |
| H3 | a searched model under the cap beats the best cap-feasible hand-built model (SparseTSF-like, FITS-like) | ants vs hand-built |
| H0 | none of the above holds: the space is saturated by simple structure or the search adds nothing | |

## 2. Tracks

One track per cap, so every arm optimises one wall. A track is a contract: `contracts/tinyts_real.toml` with one cap, a new name, `unseal_limit = 8` and a sealed-set declaration.

```bash
for cap in 100 300 1000 3000; do
  sed -e "s/^name = \"tinyts_real\"/name = \"tinyts_bench_cap$cap\"/" -e "s/^caps = .*/caps = [$cap]/" \
      -e "s/^unseal_limit = 1/unseal_limit = 8/" contracts/tinyts_real.toml > contracts/bench_cap$cap.toml
  printf '\n[sealed]\ndatasets = "catalog:sealed"\n' >> contracts/bench_cap$cap.toml
done
```

Verified: all four files pass `load_contract` (caps `[100]` .. `[3000]`, `unseal_limit` 8). The four contracts share code and data manifest, so a track's arms share one epoch.
`unseal_limit = 8` is the pre-declared number of sealed looks: 2 per track (section 6). The lifetime default is `3 x limit` = 24 (`gate.py`).

## 3. Arms

Unit of search budget: **finished ledger rows of any stage** (`--max-evals`, counted by `Ledger.count_final`). B = 1000 rows per run. Seeds 1 to 6 (`--seed`). Each run has its own absolute `--workdir`.

| arm | what | command (per track and seed) | budget |
|---|---|---|---|
| `ants` | portfolio: bandit over sweep, evo, de, cem, aco, sa, qd, tpe, random with lineage credit | `ants run --contract contracts/bench_cap$CAP.toml --workdir $PWD/runs/bench/cap$CAP/portfolio/s$SEED --arm portfolio --seed $SEED --max-evals 1000` | B |
| `random2x` | uniform random through the same filters and cascade | same, `--arm random --max-evals 2000` | 2B |
| `tpe` | Optuna TPE on s0 values (extra `search`) | same, `--arm tpe --max-evals 1000` | B |
| `evo_percap` | elitist evolution inside the cap bin ("the fair baseline", `portfolio.PerCapEvoProposer`) | same, `--arm evo_percap --max-evals 1000` | B |
| `sparsetsf_like` | `core=period`, grid of period, look-back, norm, ridge | `handbuilt.py` below | 72 specs |
| `fits_like` | `core=fourier`, grid of `fourier_k`, look-back, norm, ridge | `handbuilt.py` | 144 specs |
| `dlinear_like` | `core=linear`, `decomp=ma` | cannot enter any track: the smallest `linear` model has 34 560 parameters (L=48, H=720; 69 120 with the trend head) | n/a |
| `ridge_unconstrained` | `core=linear`, look-back 96/336/720, ridge 1/100/1e4 | `handbuilt.py`; reference row only | 27 specs |
| `seasonal_naive` | the reference itself | `rel_mse = 1` by definition (`tinyts.py`, ratio to the seasonal-naive MSE) | none |

The LLM arms (`portfolio_llm`, ...) are excluded from v0: an LLM benefit is a separate question (the server runs `portfolio_llm` and `portfolio_agent` since 2026-10-13; no result yet) ([open.md](open.md) question 2).

Hand-built grids, saved as `handbuilt.py` and run once, deterministically (no seeds). Verified in `--quick` mode (first grid point per family; the full grid, 243 evaluations of about 11 s each, was Not run):

```python
"""Usage: python handbuilt.py OUTDIR [--quick]"""
import csv, itertools, json, subprocess, sys
from pathlib import Path
from antslab.gyms import tinyts as T
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
base = T.baseline_specs()["period_linear"]
grids = {
    "sparsetsf_like": dict(core=["period"], period=[24, 48, 96], lookback=[96, 168, 336, 720], norm=["revin", "last", "mean"], ridge=[1.0, 100.0]),
    "fits_like": dict(core=["fourier"], period=[1], fourier_k=[2, 3, 4, 6, 8, 12, 16, 24], lookback=[96, 336, 720], norm=["revin", "last", "mean"], ridge=[1.0, 100.0]),
    "ridge_unconstrained": dict(core=["linear"], period=[1], lookback=[96, 336, 720], norm=["revin", "last", "mean"], ridge=[1.0, 100.0, 10000.0]),
}
rows = []
for fam, g in grids.items():
    keys = list(g); combos = list(itertools.product(*g.values()))
    for combo in (combos[:1] if "--quick" in sys.argv else combos):
        spec = {**base, **dict(zip(keys, combo))}
        d = out / f"{fam}_{len(rows):04d}"; d.mkdir(exist_ok=True); (d / "spec.json").write_text(json.dumps(spec))
        subprocess.run([sys.executable, "-m", "antslab.gyms.tinyts", "--profile", "catalog", "--stage", "s2",
                        "--spec", str(d / "spec.json"), "--out", str(d)], check=True)
        m = json.loads((d / "metrics.json").read_text())
        rows.append({"family": fam, "n_params": m["n_params"], "rel_mse": m["rel_mse"], **{k: spec[k] for k in keys}})
with open(out / "handbuilt.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r})); w.writeheader(); w.writerows(rows)
```

For each family and cap the endpoint is the lowest `rel_mse` among its grid points with `n_params <= cap`; a family with none is `infeasible` for that cap.
The `ridge_unconstrained` point `L=720, norm=last, ridge=1` reproduced `assets/frontier.json` (0.7905) when run here at s2.

## 4. Data and roles

Roles are fixed by `data/catalog.toml` and may not be chosen by any arm. Counts from `ants data list` and the catalog:

| role | datasets | families | used for in this protocol |
|---|---|---|---|
| dev | 4 (etth1, ettm1, weather, exchange) | 3 (ETT, Weather, FX) | all search; stages s0/s1/s2 on validation windows |
| confirm | 21 | 20 | E2 confirmation (blocked, section 10) |
| sealed | 10 (electricity, traffic, household, metro, sunspot x2, elec_weekly, aus_electricity, wind_farms, ercot) | 8 | one look per frozen candidate (section 6) |
| stress | 3 + 95 synthetic | | negative controls and gym qualification only; never in a headline |

Caveat from the catalog: `etth2` and `ettm2` are `confirm` but share family `ETT` with the dev sets, so they are not independent confirmation. Confirmatory support must come from the other 19 families.
Sealed families are disjoint from dev and confirm families (`docs/DATA.md`).

Stage definitions (`gyms/tinyts.py::CAT_STAGES`): s0 = etth1, H=96, 1 fold; s1 = etth1, weather, exchange, 3 folds (36 tasks); s2 = the four dev sets, H = 96/192/336/720 or the dataset's own, 3 folds (48 tasks).

## 5. Metrics

| name | definition | role |
|---|---|---|
| `rel_mse` | family-weighted geometric mean over (dataset, horizon, fold) of MSE(model) / MSE(seasonal naive at the dataset's period); below 1 beats the reference | primary |
| **endpoint** | for one run: the lowest s2 `rel_mse` among rows with `n_params <= cap`, from `ants export` | the unit of analysis |
| `rel_mae` | same with MAE | must not worsen for a win to be called (as in `registry.decide_promotion`) |
| secondary vector (s2) | `robustness_index seasonal_fidelity horizon_decay bytes` | reported, never summed into the headline |
| budget accounting | rows per stage, summed `cpu_s` per arm | reported |

`ants top`, `ants best` and the interface rank by the deepest stage reached (fixed on 2026-10-13); the endpoint can also be extracted from an export like this (verified on the demo export: 13 rows <= 1000, minimum 0.8706):

```python
import csv
cap = 1000
rows = [r for r in csv.DictReader(open("export/trials.csv"))
        if r["stage"] == "s2" and r["state"] == "done" and r["m_n_params"] and float(r["m_n_params"]) <= cap]
endpoint = min(float(r["m_rel_mse"]) for r in rows)
```

(`ants export --contract ... --workdir ... --arm portfolio --out export`.) The endpoint is a minimum over many validation evaluations, so it is optimistic. All arms select the same way, so the bias is comparable between arms,
but it is **not** an estimate of out-of-sample skill; the sealed look is.

## 6. Sealed one-look

Per track, two candidates are frozen and looked at once each: the `ants` candidate and the best competing arm's candidate (lowest median endpoint, section 7).
For an arm, the candidate is the best s2 row of the run ranked third by endpoint among the six seeds (lower median); the choice is made from dev numbers only, before any look.
Freeze with `Ledger.freeze(gym, spec, code_fingerprint(c.code_paths), {"trial_id": ...}, epoch=...)`, then `gate.seal(ledger, contract, workdir, freeze_hash, anchor_file=...)`.
That is 8 looks over the benchmark, the value of `unseal_limit`. The gate runs `--final` on **test windows of dev, confirm and sealed** sets; the sealed headline is recomputed from its `rel__<dataset>@<H>` keys with the family weights of `metrics.family_weights`.
Anchor the ledger head off-box before the first look and after each ([operations.md](operations.md)). Nothing sealed has been touched: the demo ledger shows `"unseals": 0`.

## 7. Seeds, budget, stopping rule

| item | value |
|---|---|
| seeds | 1 to 6 per (track, search arm); the six hand-built families are deterministic |
| budget | B = 1000 finished rows per run (2000 for random2x); fixed in advance, not tuned on results |
| stopping | a run stops when `ants run` prints `budget`. No early stopping, no extension, no dropping of a seed. A run interrupted by a crash is resumed with the same command (recovery is built in); a run that ends in `breaker` for non-environmental reasons is reported as is |
| interim looks | allowed for health (`ants doctor`, `status`); no arm, contract or code is changed after the first run |
| tool chain size | 4 tracks x 6 seeds x (1000 + 2000 + 1000 + 1000) = 120 000 rows |
| time (dev machine) | 1.6 s per row steady state to 4.5 s per row warm-up (measured here: 12 rows in 53.8 s) -> 53 to 150 hours, single worker. Not measured on the target machine |

## 8. Decision rules

Notation: `m_X` = median endpoint of arm X over six seeds, lower is better; gain = `(m_Y - m_X) / m_Y`.

| outcome | rule (per track unless stated) |
|---|---|
| **win over random2x** | gain >= 0.5% **and** exact two-sided Mann-Whitney U (6 vs 6) `p < 0.0125` (Bonferroni over 4 tracks) **and** >= 5 of 6 ants endpoints below `m_random2x` |
| **overall win (H1)** | win in >= 3 of 4 tracks and no track where random2x wins by the same rule |
| **H2** | the same rule against TPE and evo_percap, with Holm correction over the 8 comparisons at 0.05 |
| **H3** | `m_ants` below the hand-built endpoint of that cap by >= 0.5%, hand-built treated as a fixed value; if the family is infeasible at that cap, H3 is `not testable` there |
| **null result** | gain < 0.5% or `p >= 0.0125` against random2x in >= 2 tracks. A null result is reported with the same prominence as a win |
| **inconclusive** | exactly one track passes; or a blocker in section 10 invalidates a comparison |
| **ridge saturation** | a hand-built or tuned-ridge point within 0.5% of the best ants endpoint at the same cap: reported as "structure saturates the space at this cap" |

The smallest p-value an exact 6 vs 6 test can give is 0.0022 (checked: `scipy.stats.mannwhitneyu(..., method="exact")` on fully separated samples), below the 0.0125 threshold, so the test can reject.
What counts as a *claim*: dev-validation endpoints plus one sealed look per candidate support the words "candidate under cap N reaches X on a sealed look". They do **not** support "validated" (needs `Registry.confirm` then `Registry.challenge` at its online-FDR level, B2) or "champion" (needs a validated model plus a recorded sealed result for exactly its spec, `Registry._champion_evidence`).

## 9. Gym qualification before the first run

Run once, record the output in section 12: `pytest tests -q -m "not slow"` (the suite includes the negative controls, the noise-floor test and the memory-estimate test), then one `ants run --max-evals 60` smoke per track.
Not run here: the full suite on this machine's final tree. Also record the Spearman correlation of s0 and s2 on the first 100 specs; the demo gave 0.20 on 19 promoted specs (a small, restricted sample), which would make the cascade a weak filter.

## 10. Blockers (must be resolved or declared before freezing)

| id | blocker | evidence | proposed handling |
|---|---|---|---|
| B1 | relative `--workdir` made every trial fail | reproduced earlier here; no longer reproduces after the `cli.py` change (8 evaluations, exit 0) | resolved; keep absolute paths anyway |
| B2 | the gym does not export per-window losses with window ids, so `Registry.loss_record` / `Registry.challenge` (paired block bootstrap, online-FDR level LORD++, incumbent per cap bin via `register_baseline`) have nothing to run on | `src/antslab/gyms` has no `loss_record` or window-id output; `Registry.challenge` is called by nothing but tests | v0 makes no confirmation claim; build the export first (v1) |
| B3 | random and TPE were starved under a single cap (resolved 2026-10-13) | only 0.16 to 0.43% of uniform draws pass the memory estimate and the cap; `--arm random` and `--arm tpe` produced 0 evaluations in 100 to 120 s while `evo_percap` finished 6 | they now rejection-sample the feasible region (`Campaign._feasible` handed to `RandomProposer.accept` and `TPEProposer.accept`, tested in `tests/test_audit_regressions.py`); not yet re-timed in a full run |
| B4 | no CLI for freeze, seal, anchor, hand-built grids, endpoint extraction or the statistics | this page lists the calls | scripts live outside the core; archive them with the results |
| B5 | `est_mem_mb` under-predicts the real-data profile (560 estimated, 745 MiB measured peak at s2 here) | `docs/operations.md` | run on the development machine until measured on the 2 GB target |
| B6 | confirm `ETT` shares a family with dev | `data/catalog.toml` | count it as one non-independent source |
| B7 | the README demo table mixes s0 leaderboard values and s2 baselines | `docs/faq.md` question 11 | use the endpoint definition above |

## 11. Reproduce each arm (smoke scale, verified)

```bash
export PYTHONPATH=$PWD/src ABS=$PWD/runs/bench/cap300
ants run --contract contracts/bench_cap300.toml --workdir $ABS/evo_percap/s1 --arm evo_percap --seed 1 --max-evals 6   # budget, 37.6 s
ants run --contract contracts/bench_cap300.toml --workdir $ABS/portfolio/s1  --arm portfolio  --seed 1 --max-evals 6   # budget, 62.3 s
ants export --contract contracts/bench_cap300.toml --workdir $ABS/portfolio/s1 --arm portfolio --out $ABS/portfolio/s1/export
python handbuilt.py $PWD/runs/bench/handbuilt            # add --quick for one point per family
python -m antslab.gyms.tinyts --profile catalog --stage s2 --spec spec.json --out outdir   # any single spec, deterministic
```

`random` and `tpe` use the same command with `--arm`; They sample the feasible region by rejection (B3). Not run: any full-size run, the full hand-built grid, any sealed look.

## 12. Results (all pending)

Endpoint = median over 6 seeds of the lowest s2 `rel_mse` with `n_params <= cap` (range in brackets).

| arm | cap 100 | cap 300 | cap 1000 | cap 3000 |
|---|---|---|---|---|
| ants (portfolio) | pending | pending | pending | pending |
| random, 2B | pending | pending | pending | pending |
| TPE | pending | pending | pending | pending |
| evo_percap | pending | pending | pending | pending |
| SparseTSF-like | pending | pending | pending | pending |
| FITS-like | pending | pending | pending | pending |
| DLinear-like | infeasible (min 34 560 parameters) | infeasible | infeasible | infeasible |
| unconstrained ridge | pending (reference, no cap) | | | |
| seasonal naive | 1 by definition | 1 | 1 | 1 |

| test | cap 100 | cap 300 | cap 1000 | cap 3000 |
|---|---|---|---|---|
| ants vs random2x: gain, p | pending | pending | pending | pending |
| ants vs TPE: gain, p (Holm) | pending | pending | pending | pending |
| ants vs evo_percap: gain, p (Holm) | pending | pending | pending | pending |
| ants vs best hand-built: gain | pending | pending | pending | pending |
| sealed look, ants candidate | pending | pending | pending | pending |
| sealed look, best competitor | pending | pending | pending | pending |

Verdicts: H1 pending, H2 pending, H3 pending. Amendments and deviations: none yet.

## 13. Planned: an academic-compatible report, and one composite (nothing built, nothing run)

Two questions the primary metric cannot answer. Published work reports **MSE and MAE per dataset and horizon on standardised data**; ours is `rel_mse` against seasonal naive, family-weighted, with a parameter cap. A reader cannot put one next to the other. And a number measured only on our own protocol says nothing about how the models do on the protocols the field uses.

### 13.1 Tier A: the field's table, produced by our machinery

For every frozen candidate (and for each reference below), on the **same windows** as the standard LTSF protocol: chronological splits (ETT 12/4/4 months, which the catalog already uses for ETTh1/ETTh2/ETTm1/ETTm2; 7/1/2 for the others), z-score with training statistics only, lookback 96 (and one longer setting, 336, reported separately), horizons 96/192/336/720, **no dropped last batch**, MSE and MAE on the standardised values, per dataset and horizon, plus the average over horizons. Datasets: the four development sets for the report of the search, confirm and sealed sets for the claim.

Rules: the numbers are *produced here* and never copied from papers (their splits, batch handling and seeds differ); the report states the protocol in its first lines; Tier A is a report of frozen candidates and never feeds the search (the search sees `rel_mse` only), so it cannot be gamed by it.

### 13.2 References: what we run beside it, in-house

DLinear, SparseTSF, FITS (all linear or near-linear and small), PatchTST as a deep reference, ridge with the best lookback, seasonal naive and the mean as floors, optionally one zero-shot foundation model (Chronos or TimesFM) as a ceiling for what pre-training buys; each under the identical Tier A harness, tuned with the same budget as one ants arm (a fixed number of trials on the development data), three seeds where the method is stochastic. Parameter counts are reported next to every row, because "better at 3 000 parameters" and "better at 10 000" are different claims.

### 13.3 Tier B and C, and the composite

| tier | what | source | role |
|---|---|---|---|
| A | per-dataset, per-horizon MSE/MAE, standard splits | 13.1 | comparability with the literature; a report |
| B | `rel_mse` per size cap, family-weighted, with `rel_mae` as a guard | sections 5 and 6 | the primary result and the search objective |
| C | the same frozen models on confirm and sealed sets, and zero-shot on a held-out family | roles | generalisation; the claim |

A composite is allowed only as a **pre-registered, family-weighted geometric mean of per-dataset ratios against a stated reference**, reported next to its parts, never instead of them: one weight per data family (so the four ETT-like sets do not outvote everything else), computed on confirm and sealed sets only, with win counts and the average rank across datasets beside it and a bootstrap interval over datasets and seeds. Weights are written before the first run and changed only by a dated amendment. That composite, plus the three tiers, is **TinyTS-Bench v1**; it can be re-run on any machine with `ants bench` (planned) and the report is a single table that carries the protocol, the ants version, the epoch and the package hash.

### 13.4 What this needs

A protocol option in the gym (`profile = "ltsf"`: lookback, split, drop-last, per-dataset MSE/MAE emitted, not only the ratio), an in-house reference harness for the five references, a `bench` command that freezes candidates, runs the tiers and writes the report, and the pre-registration of weights. Estimated 2 to 3 core-days of compute and about a week of work; it starts after the equal-budget comparison (roadmap step 2) so that it measures frozen candidates of the new epoch.
