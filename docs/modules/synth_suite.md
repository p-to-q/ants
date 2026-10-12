# synth_suite

Synthetic data with known generating logic and known answers (role `stress`).

**Interface**
- `generate(family, seed)` -> (array, info); `GENERATORS` by kind: mechanism / negative_control / extreme; `REAL_BASED` surrogates (phase-randomised, block-bootstrap, AR-simulation, shuffled); `catalog_toml()`.

**Invariants**
- Every generator is a pure function of (family, seed): byte-identical regeneration.
- Negative controls (white noise, random walk, shuffled real data) must show no skill through the real pipeline; SNR families carry the oracle noise floor.
- No generator may overflow or emit non-finite values (warnings are errors in tests).
- Surrogates of real series are functions of those series: a surrogate of a sealed or confirm dataset inherits that role for access purposes (`Catalog.access_role`), although its catalog role stays `stress` ([data](data.md)).

**Notes / failure behaviour:** Synthetic results validate mechanisms and gates only; they are never headline evidence. `names("stress")` includes those surrogates; use `names_for("stress")` for sweeps that must not touch sealed or confirm information.
