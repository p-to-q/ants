# gate

Evidence gate: freeze, then spend a sealed look, then run.

**Interface**
- `seal(ledger, contract, workdir, freeze_hash, seed=12345, python=None, catalog=None, anchor_file=None)` -> sealed metrics; raises `GateError` when refused or when the run fails.
- `sealed_set_id(contract, catalog)`: hash of the declared sealed set (`[sealed] datasets = "catalog:sealed"` or a list; absent = the whole gym is one set). `contract_hash(c)` (same formula as the epoch's `contract`). `SEALED_ROLES = "dev,confirm,sealed"`.

**Invariants**
- Before anything is spent, `seal` refuses if: the whole event chain does not verify; `anchor_file` exists and the ledger no longer contains every anchored head (truncated or rewritten); the candidate was never frozen; the code fingerprint of the contract's `code_paths` differs from the frozen one; (when the freeze records an epoch) the contract hash differs from the one stored for that epoch; or (when the freeze recorded one) the catalog `protocol_hash()` differs.
- The look is spent BEFORE the final command runs (`Ledger.record_unseal`), so a crash cannot buy a second look; rules in [E7](../evaluation/E7-claim.md).
- The final command runs with `ANTS_ALLOWED_ROLES=dev,confirm,sealed` (never `stress`) and a timeout of 4 x `trial_timeout_s`.
- A crash or failed run closes its look with an error result (`record_sealed_result`); the credit stays spent. A fresh anchor is appended to `anchor_file` after a recorded result.

**Notes / failure behaviour**
- Nothing in `ants run` or the CLI calls `seal`; no deploy job writes anchors. It is library code driven by tests.
- `Campaign.freeze_best` freezes with the epoch but without the data protocol hash, so the protocol check applies only to freezes made with `protocol=` explicitly. `contracts/tinyts_real.toml` declares no `[sealed]` section, so its sealed set is the whole gym, and `unseal_limit = 1` against four cap bins is a human decision still open.
- The sealed run is the same `tinyts --final` command anyone can run by hand; without `ANTS_ALLOWED_ROLES` it reads dev, confirm and sealed windows. The mechanism is the environment variable plus discipline, not a wall (see [data](data.md)).
- Statistics-side requirement for a champion: [registry](registry.md).
