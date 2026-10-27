# series

`series run NAME` validates the declared epoch before delegating to the seed
runner. The seed manifest, directory lock, active-arm guard and arm-definition
checks remain in force. New stages use separate bases, not edited seed manifests.

`series status NAME [--json]` reports per-run state, budget and identity/chain
checks. It does not call the result/leaderboard reader by default, including for
nonempty ledgers. `--values` explicitly adds `best_per_cap`; use only at allowed
looks. Corrupt ledgers produce error rows, not fabricated empty history. Reads
are read-only and every opened ledger is closed, including failure paths.

`series plan NAME [--workers N] [--json]` estimates compute, wall time and memory.
Workers must be 1–8. CPU oversubscription does not count as additional physical
CPUs. Assumptions (90% CPU efficiency, 390 MB/worker) are stated, not measurements
of the current workload. Service CPU quota is not detected. Memory pressure is
a warning, not a prediction that the kernel necessarily swaps.

Tests: `test_series.py`, `test_series_plan.py`. Status: experimental.
