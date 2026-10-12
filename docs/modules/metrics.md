# metrics

Metrics and decision statistics (numpy only, no I/O).

**Interface**
- Aggregation: `gmre(ratios, weights)` (weighted geometric mean; empty, NaN, negative ratios or bad weights raise `ValueError`; ratios are clipped to [1e-4, 1e4] so one degenerate task cannot move the headline to 0 or inf), `family_weights`.
- Shape metrics: `seasonal_fidelity`, `horizon_decay`, `perturb` / `PERTURBATIONS`, `robustness_index`.
- Paired statistics: `block_bootstrap_pimprove` (P(improve), 90% CI of the relative gain, one-sided `p_value` = the larger of a bootstrap-SE normal p and a batch-means Student-t p), `diebold_mariano`, `indistinguishable_from_best`, `window_hash`, `student_t_sf`, `autocorr_time`, `batch_means_p`. NaN, inf, mismatched or fewer than 2 windows raise `ValueError`.
- Online FDR: `lord_gamma`, `lord_level` (LORD++).
- Skill gates: `trivial_mses` (last value, window mean, zero, seasonal naive), `beats_trivial`.
- Display and reporting: `pareto_front`, `frontier_area`, `bradley_terry`.

**Invariants**
- Calibration of the tests is itself tested over replicates (`tests/test_metrics.py`, `tests/test_redteam_metrics.py`).
- Statistics fail with an exception rather than return a verdict on malformed input.

**Notes / failure behaviour**
- `gmre` raises where an older version returned NaN; callers that fed it NaN now fail.
- Bradley-Terry is for display only.
- `frontier_area`, `pareto_front`, `bradley_terry`, `diebold_mariano` and `indistinguishable_from_best` are not called by any loop, report or board code (see [E6](../evaluation/E6-lab-health.md)).
- The p-value uses the larger of two dependence-aware estimates because a normal approximation with few independent blocks is anti-conservative; it can still be optimistic for strongly autocorrelated short comparisons.
- The module docstring cites `docs/EVAL.md`, which does not exist; the layers are in `docs/evaluation/`.
