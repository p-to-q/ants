# E7 Claim - what may we assert outside the lab?

**Emits:** publish / claim. The only layer that touches sealed data, and only once.

1. **Freeze** the candidate and the evaluator-code fingerprint into the hash chain (`ledger.freeze`). If the evidence names a trial, that trial must exist, have exactly this spec, and have been measured by evaluator code that hashes to the frozen fingerprint: evidence from an older epoch cannot be frozen under today's code. The freeze hash covers (gym, spec, code, epoch, data-protocol hash); freezing the same thing twice is a no-op.
2. **Spend** one sealed look (database-enforced, see credits below), *then* run it (`gate.seal`): a crash cannot buy a second look. Before spending, `seal` re-verifies the whole event chain, the external anchors (below), the code fingerprint, the contract hash of the freeze epoch and the catalog protocol hash.
3. **Equal-budget baselines** re-run by us under the identical protocol and search budget; published numbers are reference only.
4. **Statistics** from E2 (paired, family-supported, online FDR), plus the superior set of statistically indistinguishable models.
5. **Narrative lint:** any LLM-written text may only use numbers present in `facts.json` (`report.lint_numbers`); otherwise it is marked UNVERIFIED.
6. **Replay pack:** specs, seeds, data hashes, code fingerprint, environment lock, ledger head, anchors.

## Sealed credits (`Ledger.record_unseal`, one BEGIN IMMEDIATE transaction)
* A **look** is spent per frozen candidate: one look per freeze hash (a second look at the same candidate is only a re-roll).
* `[budget] unseal_limit` looks per **(gym, epoch, sealed set)**: different candidates frozen against the same sealed data share the budget (forking paths); a new epoch (evaluator, contract or data changed) starts a new per-epoch budget but not a new lifetime.
* `[budget] unseal_lifetime` (default 3 x `unseal_limit`) looks per **(gym, sealed set)** over all epochs, so "peek, tweak the code, peek again" is capped. Limits ratchet: a later call may lower them, never raise what an earlier recorded look used (editing the contract buys nothing). Both are human policy; set `unseal_limit` to at least the number of parameter caps if every cap bin is to get a champion.
* The **sealed set** is declared in the contract: `[sealed] datasets = "catalog:sealed"` (every dataset whose access role is sealed, including surrogates derived from sealed data) or an explicit list; without the section the whole gym is one set.
* Looks are counted as the maximum of the mutable `unseals` table and the hash-chained `sealed_evaluation` events, so deleting table rows refunds nothing. Every look and its result carry the freeze hash; `record_sealed_result` accepts exactly one result per look and none without a look; `Ledger.event` refuses the proof kinds (`sealed_*`, `candidate_frozen`, `model_*`, `alpha_wealth`). A crashed or failed run closes its look with an error result and the credit stays spent.
* `Registry.transition(..., 'champion')` requires a successful `sealed_result` for the freeze hash of exactly the model's spec.

## Roles: who may read what
`data/catalog.toml` fixes roles. `Catalog.require_roles(names, allowed)` fails closed; `Catalog.access_role` makes a surrogate (`synth_real`: a function of its base series) inherit `sealed` / `confirm` from its base; an add-on `catalog_synth.toml` may only add `stress` datasets and may not redefine one. Setting `ANTS_ALLOWED_ROLES` (or `data.role_policy`) makes `Catalog.load` refuse every dataset outside the list; `gate.seal` runs the final command with `dev,confirm,sealed`, the search must run with `dev`. `Catalog.protocol_hash()` (roles, splits, manifest) belongs in the epoch identity.

## External anchor (closes the hash chain's blind spots)
The chain hash is unkeyed: deleting the last N events, or editing history and recomputing every later hash, leaves `verify_chain` happy. `Ledger.anchor()` returns `(seq, head_hash, ts)`; `Ledger.verify_against_anchor(anchor)` fails if the event at `seq` is missing (truncation) or has a different hash (rewrite from any earlier point), or the chain before it is broken. Operation: call `Ledger.write_anchor(path)` (appends a JSON line and fsyncs) at every freeze, after every sealed result and daily, and ship the file where the ledger's writer cannot edit it: an append-only object-lock bucket, a git remote pushed from another machine, or e-mail to the owner. `gate.seal(..., anchor_file=path)` refuses to spend a look if the ledger no longer contains every anchored head and appends a new anchor afterwards. An anchor protects history up to its own `seq`; events written after the last anchor are only as safe as the host.
