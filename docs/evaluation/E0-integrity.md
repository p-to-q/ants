# E0 Integrity - is this measurement valid?

**Emits:** admit or discard an observation. Fails closed: anything unexplained is discarded and logged, never repaired.

| check | where | status |
|---|---|---|
| process exit status, timeout; `metrics.json` is a regular file under 1 MiB, parsed without NaN/Infinity, and a flat dict in which EVERY value (not only the required keys) is a finite number | `runner.run_command`, `runner.validate_metrics` | enforced |
| a stale runner cannot overwrite a reclaimed trial (fencing token = attempt number) | `ledger.finish` | enforced |
| one daemon per workdir (`flock` on `<workdir>/ants.lock`, exit 3 for the second one); a second daemon would requeue and double-evaluate the first one's live trial | `campaign.InstanceLock` | enforced for `ants run`; other commands do not take it |
| a trial is stamped with the epoch that RAN it (stamped at claim) | `ledger.claim` | enforced |
| event stream is hash-chained: an edited event, a deleted or reordered middle event and a sequence gap are detected by `verify_chain` | `ledger.verify_chain`, `verify_tail` | enforced, see limits below |
| tail truncation and a consistent rewrite (every later hash recomputed) are detected against an off-box anchor | `ledger.anchor`, `verify_against_anchor`, `write_anchor`, `gate.seal(anchor_file=)` | mechanism exists; nothing writes or ships anchors automatically |
| the archived bytes were re-read remotely and hash-match before any local deletion; every delete condition is re-checked on a fresh row, and the file is re-hashed, at delete time | `archive.archive_pending`, `janitor.execute_cleanup` | enforced |
| event `ts` and `finished_at` never go backwards, the breaker cooldown and timeouts use the monotonic clock | `ledger._stamp`, `campaign.breaker_open`, `runner` | enforced |
| the search process may read only `dev` data (`ANTS_ALLOWED_ROLES=dev`); the final run reads `dev,confirm,sealed` | `campaign._execute`, `gate.seal`, `data.Catalog.load` | enforced when the variable is set; without it `Catalog.load` is open |
| evaluator code must hash to what was frozen; sealed credit is spent *before* the run | `gate.seal` | library only: no command calls it |
| no future information: training windows' targets end before validation starts; sealed seeds disjoint from search seeds | `gyms/tinyts` + tests | tests |
| determinism: same spec and seed give the same metrics | tests | tests only; no periodic replay audit exists. `Registry.confirm` replays one spec when it is called, and nothing calls it in production |

## Known gaps (the hash chain is not a proof of history)
* The hash is unkeyed. Anyone with write access to the database can delete the last N events, or edit history and recompute every later hash, and `verify_chain` still passes. Only a copy of `ledger.anchor()` kept where the ledger's writer cannot edit it makes that visible. Events after the last anchor, and the host itself, are trusted.
* State outside the chain drives decisions: `trials.metrics` (ranking, `freeze_best`), `artifacts`, `unseals`, `models`. `UPDATE trials SET metrics=...` changes the frontier and nothing notices. `unseals` is cross-checked against events when looks are counted and `Registry.verify()` checks `models`; no audit compares `trials` with the `trial_finished` events.
* `ants doctor` runs the chain check (full below 300 000 events, otherwise the last 5000) but not the anchors.
* A gym that honestly reports wrong numbers is not detected; the epoch only proves the evaluator did not change.

## Epochs and per-trial provenance
An **epoch** is the identity of the measurement machinery: a hash of the bytes of every file in the contract's `code_paths`, the whole parsed contract, and `data/MANIFEST.sha256`. Every trial row and every `trial_finished` event carries its epoch, lineage (`origin_key`, `edit`, `parent_id`), CPU seconds and seed; the `epochs` table records Python/platform/numpy/scipy versions (not part of the identity).
* What starts a new epoch depends on the contract. `contracts/tinyts_real.toml` lists `tinyts.py`, `metrics.py`, `data.py`, `catalog.toml` and `catalog_synth.toml`, so editing the evaluator, a role or a split starts one; the two synthetic contracts list only `tinyts.py`. Raw data files count only through `MANIFEST.sha256` (they are re-hashed by `ants data verify`, not while running); processed `.npz` files do not count.
* The epoch is computed when the `Campaign` is built. Editing code while a daemon runs does not change its epoch until restart.
* Who respects it: the campaign's `History`, `seen_keys`, `frontier` and `freeze_best` filter to the current epoch, and `Ledger.freeze` refuses evidence from another epoch. Who does not: `ants best/top/show`, the board, `report.facts`, `dream`, `credit`, the plain `tpe` arm and the LLM proposers' prompts read all epochs. `ants doctor` warns when a ledger has more than one epoch.

Other integrity probes: negative-control series (white noise, random walk, time-shuffled real data) must show no skill; no model may beat the oracle noise floor of the SNR series.
