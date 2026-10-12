# proposers

Baseline arms and the LLM proposer: random, evo (tournament + credit-weighted mutation), tpe (Optuna), llm.

**Interface**
- `make_proposer(arm, c, seed, client, memory_fn, llm_share)`; arms: `random`, `evo`, `evo_percap`, `qd`, `tpe`, `portfolio`, `portfolio_llm`, `portfolio_dream`, `llm`, `llm_dream`. Every proposer returns `Proposal(spec, source, note, origin_key, edit)` so lineage is recorded.
- `rank_specs(ledger, c, arm, epoch=None)`: one row per spec at its deepest stage; with `epoch`, only evidence from that epoch. `Campaign.frontier` and `freeze_best` pass the campaign's epoch.

**Invariants**
- Every proposal is validated against the Space; LLM proposals also pass a novelty filter (exact and near-duplicate rejection).
- `LLMProposer` fills a buffer in background threads so slow calls overlap with CPU evaluation; empty buffer falls back to evo. `inflight` is released in a `finally`, so a malformed reply is counted as a failed call and cannot stall the LLM arm. Hints written by `ants hint` are stored as one cleaned line; the prompt reads the latest three `user_hint` events as they are in the ledger.
- Memory text from `dream` and hints are data inside the prompt; the reply is still only accepted through `Space.validate`.

**Notes / failure behaviour:** Unparseable or invalid LLM output is counted, not repaired. The plain `tpe` arm reads all `done` trials of the arm (every epoch) from the ledger, and the LLM proposers build their prompt history without an epoch filter; the other arms use the campaign's epoch-filtered `History` when it is passed in. `optuna` is needed only for `tpe` arms and is skipped by the portfolio when missing.
