# E43: does a learned screen find good models in fewer CPU-hours? (pre-registration)

Status: **draft until the commit that tags it** (tag `e43-prereg`); after that every change is a dated entry in section 8, never an edit of the text above it. Draft 2 (2026-10-17) follows a statistical review of draft 1 ([research/series-statistics](../research/series-statistics.md)): a power statement, a two-look design, censoring of missing values, exact intervals instead of a bootstrap of a median.
Related: [roadmap](../roadmap.md) steps 2, 42, 43 and 44, [ledger-first-look](../research/ledger-first-look.md), [screen.py](../modules/proposers.md), [operations](../operations.md) (series, sealing).

## 1. Question

The quantile rule sends the best quarter of the first-stage results (per size cap) on to the stage that costs 30 s instead of 1 s. On the pilot ledgers the first stage ranks the whole range well (Spearman 0.85 on an unselected sample) and the hard cut discards roughly a quarter to a third of the near-best results; a model fitted on the arm's own second-stage results ranked later trials at Spearman 0.93 to 0.98 on replay. **Does replacing the cut by that model (same expected workload) lower the best second-stage `rel_mse` reached for the same CPU?**

| id | hypothesis | role |
|---|---|---|
| H43a | at a budget of 50 400 CPU-seconds, the best s2 `rel_mse` among models of at most **300** parameters is lower for `portfolio_screen` than for `portfolio` | **primary** (the only confirmatory test) |
| H43b | the same for the caps 100, 1000 and 3000; the area under the best-so-far curve; the CPU-hours to come within 3% of the pooled best; the fraction of seeds with `rel_mse` of at most 0.80 at 3000 parameters | secondary, reported with intervals, uncorrected, labelled exploratory |

**What this series can and cannot see.** With 8 seeds per arm the exact Wilcoxon test cannot give a two-sided p below 0.0078, and by simulation (per-run standard deviation 0.03 to 0.05 at 300 parameters, which is the range the three pilot arms suggest, with and without the "one lineage decides the run" mixture seen in the ledgers) the difference that is detected 80% of the time is **about 0.055 to 0.10**, and 50% of the time about 0.04 to 0.06. The pilot gap between the plain arm and the two model arms at 300 parameters was 0.057. A true benefit of 0.01 to 0.03 would be missed most of the time (detected 10 to 18% of the time). So a null result of this series does **not** say the screen does nothing: it says the effect is below roughly 0.05 to 0.10; and it is not possible to show "no effect within 0.01" with fewer than a few hundred seeds. The design therefore has two looks (below), and the report states the minimum detectable effect from the observed spread.

## 2. Design

| | |
|---|---|
| arms | `portfolio` (control; definition hash 67618a671563, the arm as it ran in the pilot), `portfolio_screen` (treatment; hash 975051784e2d; screen settings: explore 0.10, kappa 0.5, min_rows 300, refit every 100, window 400, 60 trees). `portfolio_lean` is **not** in this series (three changes at once; it gets its own series, E43b, if E43 says the first stage matters) |
| seeds | **stage 1: 1 to 8** for both arms, written to the series file before the first run, interleaved over arms (seed 1 of both before seed 2 of either); **stage 2: 9 to 16**, run only if look 1 (section 6) neither reaches the efficacy boundary nor shows futility. Seeds 9 to 16 are written down now and added by a new series file `e43b.toml` that refers to this protocol |
| budget | 50 400 CPU-seconds (14 CPU-hours) per run (all stages, summed `cpu_s` of finished trials), `--max-cpu-s`. The screen needs 300 second-stage results before it takes over (about 4.5 CPU-hours on the server at 67 per hour), and the pilot says the large caps are within 3% of their final value after one to two hours and the small caps are still improving after 15 to 46; 14 hours leaves the model about two thirds of the run |
| evaluator | the real contract, epoch `5da6eeee6527` (code fingerprint e6a26c82e05dad1e, contract 4d5fd3157d35ac2d, data manifest a5c57423f87e4007); the series refuses to start if the computed epoch differs |
| cost | stage 1: 16 runs of 14 CPU-hours = 224 CPU-hours, two at a time on two vCPUs, about 4.7 days; stage 2 (if triggered): the same again |
| data | the four development datasets only; confirm and sealed data are not touched (frozen finalists go through the gate later, roadmap step 27) |
| machine | the 2 vCPU server, 2 runs at a time; one `ants run` per workdir; budget counted in CPU-seconds, so load does not change a run's budget, only its wall time |
| model arms | none: no model calls in this series (the model operators are a separate series) |
| sealed pilot | the three pilot ledgers are sealed in a bundle and **excluded** from every test below: they were used to design this experiment |

## 3. Endpoints, defined exactly

* *Value at budget B* for a run and a cap: the minimum `rel_mse` over finished **s2** trials with `n_params` at most the cap, among those whose finish time is not later than the moment the run's cumulative CPU (sum of `cpu_s` over all finished trials, taken in finish order) reaches B. A run with no such trial has the reference value 1.0 (seasonal naive): **censored, not dropped**, because dropping favours the arm that finds more; the complete-case result is reported as a sensitivity row. Times to a target are right-censored at the budget in the same way.
* *Primary:* value at B = 50 400 s, cap 300.
* *Workload parity (a check, not an endpoint):* the share of the run's CPU spent at s1; if the median share of `portfolio_screen` differs from that of `portfolio` by more than 20% (relative), the arms did not spend alike and the comparison is read with that in view.

## 4. Analysis (run at each look; `scripts/analyze_series.py`)

* **Primary:** two-sided **exact Wilcoxon signed-rank test** on the seed pairs (screen minus control) of the censored value at B = 50 400 s, cap 300, at the per-look level **0.0294** (two equally spaced looks, Pocock 1977, overall level about 0.05; the boundary is quoted from the literature, not re-derived here). Because the exact test is discrete, its realised level is lower still.
* **Effect size and interval:** the Hodges-Lehmann estimate with the **exact** distribution-free interval from the Walsh averages (coverage at least 95%; 96.1% at 8 pairs), the probability of superiority **A12** (the chance that a screen run beats a control run, ties half) with a bootstrap interval, and the exact sign-flip permutation p of the mean difference. The median of 8 differences is reported, but a bootstrap interval of a median over 8 values is not used (liberal at this size).
* **Reading** (replaces draft 1's "no effect" branch, which could not be reached with these numbers): *evidence for H43a* if p is below the per-look level and the median difference is negative; *futility at this look* if the median difference is not in favour of the screen; otherwise *inconclusive: the effect, if any, is smaller than the minimum detectable effect*, which the report computes from the observed spread of the paired differences.
* Sensitivity: complete cases only; the unpaired Mann-Whitney test; the same endpoint at 14 400 and 28 800 CPU-seconds.
* Secondary and exploratory rows (other caps, area under the best-so-far curve, CPU-seconds to within 3% of the pooled best, a post hoc target): tables with intervals; the number of rows is printed; no p-value of them is a confirmation.
* Restarts against one long run (roadmap step 44): descriptive; for K = 8 there is a single subset and no variance.
* Run it as `python scripts/analyze_series.py <series base dir> --out report.md --csv runs.csv`. The script and the tests that pin its statistics are in the repository (`tests/test_analyze_series.py`).

## 5. Validity checks (a run that fails one is reported, not silently dropped)

Every ledger's epoch equals 5da6eeee6527; the arm definition hash is constant inside each ledger; the hash chain verifies; no run was restarted more than three times by the service; the control's seed-1 run is compared with the pilot's `portfolio` (same arm, same seed, same evaluator): its first proposals should repeat the pilot's, but the trajectories need not stay equal (`cpu_s` is measured and two runs share the machine), so the comparison is of the first 200 proposals, and a difference is a finding about determinism, not an outcome of E43. The family of validity checks is unchanged by the number of arms.

## 6. Looks

Two, fixed now. **Look 1** when the 16 runs of stage 1 have finished: the primary test at level 0.0294. If it rejects: stop, report. If the median paired difference is not in favour of the screen: stop for futility, report. Otherwise run stage 2 (seeds 9 to 16, 16 more runs) and do **look 2** on all 16 pairs at level 0.0294. Between the looks only `ants series status` (health, progress) is read; no decision depends on a comparison of arms except the two above.

## 7. What each outcome leads to

| outcome | next |
|---|---|
| evidence for H43a at look 1 or 2, workload parity holds | the screen becomes the promotion rule of the next series; step 43 closes; the leave-one-operator-out series (step 17) runs with it |
| futility at look 1 | the screen stays an option (`portfolio_screen`) and is not adopted; the first stage is kept and the epoch-3 package (roadmap 45) goes ahead on other grounds; the report says the effect is probably below the minimum detectable effect |
| inconclusive at look 2 | the effect, if there is one, is below the minimum detectable effect of 16 pairs (about 0.04); the screen is not adopted on this evidence; a larger series is a new protocol |
| a validity check fails for several runs | the series is repeated after the cause is fixed; the failed runs stay in the record |

## 8. Deviations

- 2026-10-17, before tagging: draft 2 replaces draft 1 after the statistical review (power statement, two looks, exact intervals, censoring).
