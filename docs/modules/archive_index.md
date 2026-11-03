# archive_index

Past is the last terminal page (`9`). Its job is to help an operator revisit
a research round and locate its evidence, not rank unrelated experiments.
The body opens with a compact retained-round count and cache notice; the tab
already supplies the page name. It has no standing introduction or repeated
title. The list, selected-round preview and Enter details retain their structure.
Unreadable history says unavailable, not zero rounds. Empty/error states keep
their indexing/repair instructions, and recorded receipts remain explicitly
different from a live backup check.

## Information and actions

The list groups a whole round rather than individual trials. Select with arrows
or Page Up/Down; Enter opens the immutable summary; Esc returns to the same row.
The overview shows the question, date, round kind, search-stage metric, parameter
cap, epoch and eligible-run coverage. Values are descriptive medians of per-run
best feasible results at the deepest search stage. Missing runs are counted and
excluded, never imputed. Pilot results remain exploratory. There is no ranking
across epochs or rounds, and these summaries are not confirmatory analyses.

Details retain per-run parameters, trial identifiers, chain heads, all cap results,
the bundle checksum and its location. Off-host notes are operator-recorded
receipts, not a live availability check or an independent proof of replication.
No restore, delete, pause or hint action is attached to this page.

## Cache and interface

`ants archive index --bundle BUNDLE --contract FROZEN_CONTRACT --cache DIRECTORY`
verifies a private copy of the bundle, then extracts it and writes one round JSON
and an atomic `index.json`. The default kind is `snapshot`: it omits all research
metrics so an operational backup does not expose a running comparison. Use
`--kind pilot|completed|aborted` deliberately for result summaries. Such summaries
require the frozen contract/source epoch to match the ledger. Optional flags are
`--title`, `--location`, and `--offsite-note`. `ants archive list --cache DIRECTORY`
reads the index. It never queries live experiment ledgers.

The viewer uses `ANTS_ARCHIVE_INDEX`, then the personal archive index if present,
then `/var/lib/ants/archive-index/index.json`. Files are limited to 2 MiB and the
index to 2000 rounds. Writers lock the index and refuse to replace a round with a
different bundle. Same-bundle indexing is idempotent. Existing cache metadata is
preserved; changing a title or receipt requires an explicitly managed new record.

## Failure behaviour

Missing index: an empty page with the indexing command. Invalid or unreadable
index: an actionable error, not a fabricated empty history. Invalid detail or
checksum mismatch: a read-only error pane, with Esc to return. Regular-file,
bounded-read and safe-ID checks reject pipes, symlinks and traversal. An uncapped
contract (no `cap_metric` and no `caps`) indexes with a null focus cap and an
empty preview instead of crashing. Index failure never modifies a running
experiment. Cache verification does not replace
bundle verification or a restore drill; the original bundle remains the evidence.

Tests: `tests/test_archive_index.py`, `tests/test_tui.py`. Status: experimental.

## Next-version plan

The current implementation still includes selected-round explanatory prose. [Past's next-version specification](../research/past-next-version.md) moves it into details/help and replaces the preview with structured facts. [The unified catalog](../research/experiment-catalog.md) adds study/stage/run identities and proposed-plan projections. These are planned schema/UI changes; the current index format, limits and behaviour above remain accurate.

The current populated Past preview now uses compact scope/coverage/snapshot tags and a single timestamped receipt line. Long interpretations and locations remain behind Enter. Off-host notes use neutral styling because recorded text is not a current verification. The existing cache schema and result semantics are unchanged; the broader catalog/route/plan redesign remains future work.
