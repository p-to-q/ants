# landscape

Landscape diagnostics for the board and the paper.

**Interface**
- `niche_table` (leader/riser/stalled/young; a niche with fewer than 16 evaluations is `young`), `ruggedness` (parent/child correlation, None below 10 pairs), `plateau_fraction`, `diagnostics`.
- `diagnostics` returns evaluations, feasible count, ruggedness, plateau fraction, stagnation length, operator counts, delayed credit, retired levers, the niche table and the stepping-stone statistics.

**Invariants**
- Read-only.

**Notes / failure behaviour:** `diagnostics` does not compute frontier area, proxy fidelity, failure rates or judge calibration; those parts of [E6](../evaluation/E6-lab-health.md) are not run.
