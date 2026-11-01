# Past: results, routes and evidence

Next-version specification, linked to the [unified experiment catalog](experiment-catalog.md) and [saved-model view](leaderboard-saved-models.md).

## Content hierarchy

Remove standing narrative paragraphs from the default populated page. Existing code already removes the redundant Past heading and graveyard introduction, but the selected-round preview still prints explanatory result/receipt prose. Replace that preview with terse labelled facts and move definitions, caveats and repair commands to Enter details or help. Preserve compact scope tags such as `exploratory`, `cached`, `partial coverage` and `unreviewed`; deleting caveats must not turn a descriptive number into a scientific conclusion.

The page has three levels:

1. Timeline/list: study, stage, date, execution/acceptance status, run coverage, short result disposition and artifact availability. Paginate all history; there is no ten-model or ten-round limit.
2. Selected study/stage: question in one line, registered comparator, changed factors, available result/report, route summaries and saved-model count. Wide terminals use list/detail columns, narrow terminals use stacked sections. Retain the visual/key conventions of existing pages; no large title, hero number, decorative prose or forced animation.
3. Enter details: arm/seed run inventory, full protocol, technical definitions, statistical analysis, missingness/failures, source identities, file locations and export/restore guidance. Full JSON is an optional raw view rather than appended automatically to every human-readable record.

For E43 show `E43 → E43a / E43b → arm × seed`. The parent owns the combined final decision. A stage entry links both its stage report and the combined study. A backup snapshot sits under Artifacts, not beside studies as a new experiment.

## What to show and what to save

| information | default view | durable source |
|---|---|---|
| date, status, completed/expected runs | list | stage manifest and execution receipts |
| question and changed factors | selected detail | protocol/plan revision |
| primary effect, uncertainty, decision | selected study, only if reviewed | immutable statistical report and input identities |
| descriptive best/median and coverage | scoped detail, clearly tagged | reproducible summary plus ledger reference |
| routes explored and their representative models | selected detail, expand for all | route definition/version, membership and candidate links |
| predecessor/comparator and differences | selected detail | explicit comparison record and compatibility checks |
| saved models, exact weights available | count and link to Saved | candidate collection and artifact receipts |
| interrupted/failed trials and recurring failures | compact count, expand | ledger/events/log archive |
| CPU budget, elapsed time, evaluation counts | selected detail | charged-ledger and service receipts; do not conflate CPU and wall time |
| backup availability and verified-at time | short dated status | local/offsite verification receipts |
| chart, sorting, paging, short display labels | derived | regenerable cache; never sole scientific evidence |
| raw data, complete logs, weights and source | not loaded into default view | separately hashed artifact bundles with retention policy |

A technical route initially means an explicit structural signature using active core/ensemble/decomposition/normalization/prior settings. Version the classifier. Group within the same evaluation identity and keep sample/evaluation counts and best-model references. This is an explanatory grouping, not a causal claim that a lever helped. No route counts are guessed from nine proposer names. Parameter-space exploration and search operator provenance are separate dimensions.

The selected detail can answer “what changed?” with at most a few configuration changes and the registered effect. A full route comparison opens on demand. Keep successful, failed and abandoned routes where evidence exists; do not erase them because the current ten models omit them.

## Comparison and export

Default comparison is the declared scientific reference. A user may choose another record, but the compatibility guard decides whether numeric deltas are meaningful. For incompatible protocols, show changed settings and identities without a winner arrow. Preserve matched-unit inputs and report hashes for accepted comparisons. Never automatically compare E43b against E43a as if they were independent arms.

Separate three export operations: report/metadata only, selected model package, and full experiment evidence. The first is small and shareable after private-path review; the second links to Saved's exact variant and receipt; the third can include private logs/data and needs explicit scope. All produce a manifest/checksums and verification result. No export button secretly reruns training or downloads missing raw data.

The index stores references, not duplicate weights or complete ledgers. Immutable evidence lives outside the disposable cache. Batch indexing verifies bundles, produces summaries and route memberships, then publishes a new cache generation atomically. Readers keep the last valid generation during an update and show its age. A bad record is unavailable, not empty or score zero. Missing receipts and stale receipts are visibly distinct from current verified availability.

## Implementation sequence

Implement catalog/schema and read/export API first; migrate existing verified round caches; produce fixture screens for E43, old pilots, proposed plans and failure states; then replace the populated Past preview with the compact layout. Finally connect route summaries and model links. Retain the old index reader during migration. Do not modify historical ledgers to manufacture the new schema.

The page should feel useful through density and traceable relationships: one navigable study timeline, a small technical fingerprint and exact links to reports/models. More information belongs in structured drill-down, not more paragraphs.
