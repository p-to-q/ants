# bench

In-process harness and analytic 'trap' landscape for testing the search layer.

**Interface**
- `drive(proposer_factory, c, n_evals, seed, batch, noise, fn)`, `trap(spec, noise, rng)`, `make_contract`

**Invariants**
- Known optimum, deceptive basins, ripples, dormant levers. No subprocesses.

**Notes / failure behaviour:** A mechanism demo, not evidence of general superiority; its `History` has no epoch filter (single run, one ledger in a temp directory).
