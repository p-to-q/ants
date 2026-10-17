# A first look at the three server ledgers (2026-10-17)

Roadmap step 21, first pass. Read-only, from copies of the three ledgers taken with SQLite's backup API on the server on 2026-10-17 (the live files were opened with `mode=ro`; nothing on the server was changed), all inside epoch `5da6eeee6527` (started 2026-10-13). One seed per arm. Reproduce with `python scripts/ledger_first_look.py <ledger copy>`; the script needs numpy, scipy and scikit-learn, which ants itself does not.

What this is not: a comparison of arms (one seed each), a forward test of anything (the surrogate numbers replay recorded trials), or a statement about candidates no operator ever proposed.

## 1. The numbers

| | `portfolio` | `portfolio_llm` | `portfolio_agent` |
|---|---|---|---|
| finished trials (s0 / s1 / s2) | 14 402 / 3 717 / 1 139 | 26 681 / 6 328 / 1 435 | 24 819 / 6 095 / 1 649 |
| CPU-hours (s0 / s1 / s2) | 3.6 / 30.9 / 21.9 | 5.1 / 34.0 / 15.4 | 5.0 / 30.6 / 17.6 |
| mean seconds per trial at s1 / s2 | 29.9 / 69.2 | 19.3 / 38.6 | 18.1 / 38.5 |
| Spearman s1 against s2 | 0.998 | 0.995 | 0.996 |
| s2 minus s1 (mean, sd) | +0.009, 0.016 | +0.008, 0.016 | +0.005, 0.007 |
| Spearman s0 against s1 (promoted trials only) | 0.116 | 0.263 | 0.135 |
| best at most 100 / 300 / 1000 / 3000 parameters | 0.922 / 0.851 / 0.788 / 0.784 | 0.828 / 0.797 / 0.783 / 0.777 | 0.824 / 0.794 / 0.781 / 0.781 |
| CPU-hours when the best was reached, caps 100 / 300 / 1000 / 3000 | 46 / 15 / 48 / 52 | 26 / 23 / 44 / 19 | 24 / 22 / 52 / 52 |
| CPU-hours to come within 3% of the final best, same caps | 30 / 1.1 / 1.7 / 1.9 | 5.8 / 2.4 / 0.5 / 0.6 | 2.4 / 2.0 / 1.1 / 1.1 |
| root of the 50 best s2 results (distinct roots) | `aco` (1) | `tpe` (1) | `tpe` (1) |
| median chain length from that root | 23.5 mutations | not computed | not computed |
| model calls: total / ok | none | 4 040 / 3 278 | 6 000 / 5 714 |

Operators by the share of their s1 trials that fall in the s1 top decile (an operator is the one that first proposed the specification):

| operator | `portfolio` | `portfolio_llm` | `portfolio_agent` |
|---|---|---|---|
| `evo` | 0.44 (839 trials) | 0.54 (978) | 0.55 (1107) |
| `de` | 0.001 (1140) | 0.066 (1562) | 0.001 (1521) |
| `tpe` | 0.000 (1043) | 0.000 (1262) | 0.000 (1818) |
| `aco`, `qd`, `cem`, `random`, `sa`, `sweep` | 0.000 to 0.006 | 0.000 to 0.006 | 0.000 to 0.001 |
| the model operator | | 0.003 (327) | 0.004 (262) |

The surrogate replay (random forest on the specification's parameters, fitted on the first 70% of finished s1 trials, ranking the last 30%): Spearman 0.934 / 0.962 / 0.981; evaluating only the 30% best predicted keeps all of the top-decile results in each arm; fitted on trials not started by `evo` and tested on those started by `evo`, 0.70 in `portfolio` (the one group split run).

## 2. What it says

1. **The top of every arm is one lineage.** The 50 best s2 results of each arm descend from a single root specification through a chain of about two dozen mutations. In `portfolio` the root was proposed by `aco`, in the other two by `tpe`. Evolutionary mutation does the climbing; the other operators, between them, produce almost no top-decile results at s1, though they supply roots. (`origin_key` is recorded for 45% of the `portfolio` s0 trials, so chains can be broken; the top chains were traced to a root in every arm.)
2. **Which root won decided the small caps.** At most 100 parameters the plain portfolio ended at 0.922 and the two arms with a model at 0.824 and 0.828; at most 300, 0.851 against 0.794 and 0.797; at most 1000 and 3000 all three are within 0.012. With one seed each this may be luck of the root or an effect of the model operators; it is not known, and it is the reason step 2 comes before anything is built.
3. **The second deep stage repeats the first.** s2 is 28 to 39% of the CPU of an arm, ranks as s1 does (Spearman 0.995 to 0.998) and is on average 0.005 to 0.009 higher. s1 is the stage that costs the most (55 to 62%); in `portfolio` 38% of its CPU went to trials worse than the s1 median and 60% to trials outside the top quarter.
4. **The first stage ranks well across the range and badly at the top.** Among promoted trials s0 agrees with s1 at Spearman 0.12 to 0.26, but that is range-restricted. Step 42 sent 300 specifications drawn uniformly from the 10 685 first-stage results of `portfolio` that never reached s1 through s1 (on the development computer; same epoch, same evaluator fingerprint): over this sample s0 and s1 agree at Spearman 0.85 (95% bootstrap interval 0.80 to 0.89). So s0 separates good from poor; it does not order the good ones, and a hard cut (the top quarter per cap) discards some of the best. Four of the 300 discarded specifications reached an s1 value of 0.7818 or better, which is the level of the s1 top decile of the 3 717 promoted ones; scaled to the pool that is about 140 such specifications (interval 0 to 280) against 372 among the promoted, roughly a quarter to a third of the near-best results lost at the first stage, with a wide interval. The best of the discarded ones (s1 0.7795 to 0.7831, six of the eight best) are `evo` mutants with `norm = last`, `ens = 3`, lookback 625 to 710 and about 2 070 parameters, with s0 between 0.849 and 0.864 against a median of 0.843 for the promoted: they missed the cut by a hair and are as good at s1 as anything the arm promoted. The `tpe` operator, which sees only s0 values, had 24.6% of its s0 results in the top decile of s0 and none of its trials in the top decile at s1. Data: `docs/research/data/exp42_unselected_s1_portfolio.csv`; scripts `exp_unselected_s1.py` and `exp_unselected_s1_report.py`.
5. **A learned screen ranks well on replay.** The forest's Spearman of 0.93 to 0.98 and its recall of the whole top decile at 30% of the trials come from ranking later trials of the same runs, which are largely descendants of earlier ones, so some of it is neighbourhood; the group split lowers it to 0.70 and does not remove it. The claim to test forward is that a screen of this kind saves a large part of the s1 CPU at equal quality (step 43).
6. **Diminishing returns are steep for the large caps.** At most 3000 parameters every arm was within 3% of its final value after 0.6 to 1.9 CPU-hours of 54 to 56, and within 1% after 2.7 to 4.3; the small caps took 1 to 30 hours to get within 3% and were still improving at 15 to 46.
7. **The grammar is narrower than it looks.** In `portfolio`, 13 309 of 14 402 s0 trials and 3 713 of 3 717 s1 trials use the `period` core; `fourier` and `basis` reached s1 four times between them (rel_mse about 1.37, worse than the seasonal-naive reference), and 103 `linear` specifications were tried at s0 and none promoted (a dense linear map cannot fit under 3000 parameters). In `portfolio_agent` the `fourier` core got 922 s1 trials and its best, 0.837, trails `period` (0.777). So the four-core grammar was, in these runs, one core with levers in one arm and two in the others.
8. **Two different mechanisms reach about 0.78.** The `portfolio` lineage leans on `pretrain = pool` (a prior fitted on the training windows of other development families, never the target's family or confirm or sealed data; read in `tinyts._pool_prior`) and `prune`; the `portfolio_agent` lineage on `norm = last` with lookback and period; the `portfolio_llm` forest also uses `ridge`, `rank` and `distill_lambda`. More than one basin exists at 3000 parameters.
9. **The model operators did not reach the top by themselves.** 262 and 327 of their proposals reached s1; one each reached the s1 top decile. Their s1 median (0.818 and 0.860) is better than that of `tpe`, `de`, `aco` and `qd`. Model calls: 6 000 with 286 failed in `portfolio_agent`; 4 040 with 762 failed in `portfolio_llm` this day (the 68% failure seen in one hour earlier is not the average: 19% over the whole run). The agent's proposals are not near-duplicates of earlier ones (median normalised distance to the nearest earlier specification 0.072, against 0.004 for `evo` and 0.017 for `tpe`).

## 3. What this changes

The nine operators are not an ecology of nine in these runs: one climbs, the others seed it, and several take about 55% of the s1 trials without reaching the top decile. That is not yet an argument against them (their seeds may be what the climber needs; the leave-one-operator-out ablation of step 17 is the test), but it is an argument for testing the simpler shape first: local search from several independent roots, a learned screen in place of the first stage, a single trusted deep stage. The first stage is a usable coarse ranker that should not make hard cuts at the top. The plan is in [the roadmap](../roadmap.md) (steps 42 to 45), and the literature behind each part is in [search-landscape](search-landscape.md).

## 4. Not done

Per-cap lineage analysis; the cost model (which levers make a trial expensive: `ens` 1 to 3 gives 12.9, 21.5 and 33.5 seconds at s1 in `portfolio`); the share of s1 trials that are near-duplicates; lever interactions; and anything about the `final` or confirm data (the gate has not been used).
