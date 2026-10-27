# candidates

A private versioned store of development finalists, separate from `Registry`
promotion and the read-only terminal. `ants candidates sync` takes explicit
`--contract`, `--workdir`, `--arm`, `--store`. It verifies a consistent read-only
ledger snapshot, its chain and evaluator identity, then selects the best
completed deepest-stage trial at each cumulative parameter cap. Missing,
nonnumeric, negative and over-cap sizes are excluded. Spec keys are rechecked.

`watch` repeats that operation at `--interval` seconds (1–3600, default 60),
exits cleanly on SIGTERM/SIGINT and prints changes. It is explicitly started,
not hidden inside the viewer or campaign. Full chain/row scans cost O(ledger
size); choose an appropriate interval and isolated process budget for large
ledgers. This command is not enabled on the frozen E43 server by this change.

Records under `versions/` keep configuration, metrics, trial/epoch/arm/contract
identity, source hashes and the observed chain head. A locked atomic index
replaces only the current-best pointer in the same run/identity/cap scope.
Earlier versions remain. Unchanged leaders do not create records or rewrite
the index. A rewound source cannot silently replace a recorded best with a
worse one; retained incumbents are reported. Invalid caches and cross-scope
pointers fail, rather than appearing empty. Files are bounded and symlinked
store/record targets are refused. No automatic version deletion is implemented.

`list --store` and `show ID --store` verify cached records. IDs identify the
candidate content; a separate checksum also covers the first preservation
receipt. These unkeyed checks are corruption detection, not independently
signed scientific evidence. Chain verification does not reconcile the mutable
trial table against events (roadmap 29).

Records explicitly say `weights_preserved=false` and `release_ready=false`.
The current TinyTS gym wrote metrics, not original fitted weights; this store
cannot reconstruct a historical checkpoint. `candidates refit` explicitly
creates a separate train-only artifact; see [model_artifact](model_artifact.md).
No source record, trial artifact or Registry state is changed by preservation.

Tests: `test_candidates.py`, `test_candidate_views.py`. Status: experimental.
