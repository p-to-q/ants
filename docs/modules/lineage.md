# lineage

Ancestry and stepping-stone statistics.

**Interface**
- `ancestry`, `percentiles_at_time`, `stepping_stats` (non-leader ancestor fraction, worst ancestor percentile, lead change).

**Invariants**
- Champions must be feasible (over-cap records are excluded from the top set).

**Notes / failure behaviour:** Tests the claim that the next champion need not descend from the current leader. Works on `History.first`, so it only sees first-stage records still in memory (old ones are evicted after `max_records`) and the epoch the history was built for.
