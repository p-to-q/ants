# E43: does a learned screen find good models in fewer CPU-hours? (pre-registration)

Status: **draft until the commit that tags it** (tag `e43-prereg`); after that every change is a dated entry in section 8, never an edit of the text above it.
Related: [roadmap](../roadmap.md) steps 2, 42, 43 and 44, [ledger-first-look](../research/ledger-first-look.md), [screen.py](../modules/proposers.md), [operations](../operations.md) (series, sealing).

## 1. Question

The quantile rule sends the best quarter of the first-stage results (per size cap) on to the stage that costs 30 s instead of 1 s. On the pilot ledgers the first stage ranks the whole range well (Spearman 0.85 on an unselected sample) and the hard cut discards roughly a quarter to a third of the near-best results; a model fitted on the arm's own second-stage results ranked later trials at Spearman 0.93 to 0.98 on replay. **Does replacing the cut by that model (same expected workload) lower the best second-stage `rel_mse` reached for the same CPU?**

| id | hypothesis | role |
|---|---|---|
| H43a | at a budget of 28 800 CPU-seconds, the best s2 `rel_mse` among models of at most **300** parameters is lower for `portfolio_screen` than for `portfolio` | **primary** (the only confirmatory test) |
| H43b | the same for the caps 100, 1000 and 3000; the area under the best-so-far curve; the CPU-hours to come within 3% of the pooled best; the fraction of seeds with `rel_mse` of at most 0.80 at 3000 parameters | secondary, reported with intervals, uncorrected, labelled exploratory |
| H43c | `portfolio_lean` (noisy operators dropped, reserved evo+qd pair, discounted bandit) differs from `portfolio` | exploratory; three things change at once, so it can only motivate an ablation |

## 2. Design

| | |
|---|---|
| arms | `portfolio` (control; definition hash 67618a671563, the arm as it ran in the pilot), `portfolio_screen` (treatment; screen settings: explore 0.10, kappa 0.5, min_rows 150, refit every 100, window 400, 60 trees), `portfolio_lean` (exploratory) |
| seeds | 1 to 8 for every arm, written to the series file before the first run; run order interleaved over arms (seed 1 of every arm before seed 2 of any) |
| budget | 28 800 CPU-seconds per run (all stages, summed `cpu_s` of finished trials), `--max-cpu-s`; the pilot says the large caps are within 3% of their final value after one to two CPU-hours and the small caps are still improving after 15 to 46: eight hours covers the first and part of the second |
| evaluator | the real contract, epoch `5da6eeee6527` (code fingerprint e6a26c82e05dad1e, contract 4d5fd3157d35ac2d, data manifest a5c57423f87e4007); the series refuses to start if the computed epoch differs |
| data | the four development datasets only; confirm and sealed data are not touched (frozen finalists go through the gate later, roadmap step 27) |
| machine | the 2 vCPU server, 2 runs at a time; one `ants run` per workdir; budget counted in CPU-seconds, so load does not change a run's budget, only its wall time |
| model arms | none: no model calls in this series (the model operators are a separate series) |
| sealed pilot | the three pilot ledgers are sealed in a bundle and **excluded** from every test below: they were used to design this experiment |

## 3. Endpoints, defined exactly

* *Value at budget B* for a run and a cap: the minimum `rel_mse` over finished **s2** trials with `n_params` at most the cap, among those whose finish time is not later than the moment the run's cumulative CPU (sum of `cpu_s` over all finished trials, taken in finish order) reaches B. A run with no such trial has no value (it counts as a missing pair and is reported).
* *Primary:* value at B = 28 800 s, cap 300.
* *Workload parity (a check, not an endpoint):* the share of the run's CPU spent at s1; if the median share of `portfolio_screen` differs from that of `portfolio` by more than 20% (relative), the arms did not spend alike and the comparison is read with that in view.

## 4. Analysis (run once, when all 24 runs have finished)

* Primary: two-sided exact Wilcoxon signed-rank test on the 8 seed pairs (screen minus control), alpha 0.05; effect: median paired difference and a percentile bootstrap 95% interval over seeds (10 000 resamples).
* Reading: *evidence for H43a* if p is below 0.05 **and** the median difference is at most -0.005; *no effect* if the interval lies within plus or minus 0.01; *inconclusive* otherwise. Anything else is not a finding.
* Sensitivity: the unpaired Mann-Whitney test on the same values; the same endpoint at 7 200 and 14 400 CPU-seconds.
* Secondary and exploratory results: tables with interval estimates, no p-values presented as confirmatory.
* Restarts against one long run (roadmap step 44), from the same runs: the best of K seeds at 1/K of the budget against one seed at the full budget, K = 2, 4, 8, by cap.
* The script is `scripts/analyze_series.py` (committed with this document; run as `python scripts/analyze_series.py <series base dir> --out report.md`).

## 5. Validity checks (a run that fails one is reported, not silently dropped)

Every ledger's epoch equals 5da6eeee6527; the arm definition hash is constant inside each ledger; the hash chain verifies; no run was restarted more than three times by the service; the control's seed-1 run is compared with the pilot's `portfolio` (same arm, same seed, same evaluator: its first 28 800 CPU-seconds should reproduce the pilot's trajectory; a difference is a finding about determinism, not an outcome of E43).

## 6. Looks

During the run only `ants series status` (health, progress) is read. No stopping rule depends on a comparison of arms. The comparison is computed once, at the end.

## 7. What each outcome leads to

| outcome | next |
|---|---|
| evidence for H43a, workload parity holds | the screen becomes the promotion rule of the next series; step 43 closes; the leave-one-operator-out series (step 17) runs with it |
| no effect | the screen stays an option (`portfolio_screen`), is not adopted; the first stage is kept and the epoch-3 package (roadmap 45) goes ahead on other grounds |
| inconclusive | a second series with more seeds, written as a new protocol; the interval says how many |
| a validity check fails for several runs | the series is repeated after the cause is fixed; the failed runs stay in the record |

## 8. Deviations

(none yet)
