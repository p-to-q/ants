# E1 Quality - how good is this model at this fidelity?

**Emits:** advance (S0 -> S1 -> S2) or stop. Cheap, deterministic, run on every candidate.

* **Headline** `rel_mse`: geometric mean over (dataset, horizon) of MSE(model) / MSE(seasonal naive); < 1 beats the reference.
  Scale-free, symmetric in gains and losses, and each independent source family counts once (`metrics.gmre`, `family_weights`). `gmre` clips each ratio to [1e-4, 1e4] and raises on NaN or negative input.
* **"Beats the reference" is not skill.** On white noise the mean forecaster scores about 0.5 and on a random walk the last value about 0.8, and the seasonal-naive spec itself scores just under 1. E1 does not test for skill; the trivial-forecaster gate (`metrics.beats_trivial`, `Registry.confirm`) belongs to E2 and is not run in the search loop.
* **Hard cap is a gate**, not a term: parameters counted at horizon 720; over-cap candidates are archived as stepping stones
  but are never leaders, elites, parents-by-rank or reward signals (`history.feasible`). A free analytic cost screens most of them out before any CPU is spent (`campaign._refill`, `stone_quota`).
* **Secondary vector (S2+):** bytes, `robustness_index`, `seasonal_fidelity`, `horizon_decay`, `rel_mae`. Used as Pareto
  dimensions and tie-breaks, never summed into the headline.
* **Cascade:** after S0 and S1 a trial is promoted if it is in the best `promote_quantile` of its stage (`[cascade]`, 0.25 in `tinyts_real.toml`); while the stage has fewer than `max(3, min_history/3)` results everything is promoted, and below `min_history` the better half. Everything is epoch-filtered. Proxy fidelity (does S0 rank like S2?) is **not run**: no script or test computes the correlation, so a long run starts without that check.

## Rolling-origin validation (found by a protocol-parity check)
On ETTh1 the validation MSE is about twice the test MSE and the ranking of baselines flips between them (validation: FITS-like < period-linear; test: the reverse).
A single fixed validation segment therefore misleads the search. Stages S1/S2 of the real-data gym evaluate `folds` expanding-window origins over the last 40% of train+val (fold i trains
only on targets before its block) and across several source families; S0 stays a single cheap block on ETTh1 at horizon 96. Test windows are never used by validation modes (tested). The sampled validation windows are deterministic and fixed per stage, so a long search can still overfit them before E2.

## Reference forecaster per dataset
The reference is the seasonal-naive forecast at the dataset's own period (`ref_period` in the catalog); `exchange` has none (period 1 = last value).
