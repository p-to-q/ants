# One experiment catalog, several views

Next-version design decision. No automatic execution is implied by a catalog entry.

## Scientific identity

Separate four objects:

- **Study**: one question and protocol, such as E43.
- **Stage / series**: an executed or planned configuration, such as E43a (seeds 1–8) and E43b (seeds 9–16). E43's registered second look combines the stages; they are not independent rival hypotheses.
- **Run**: one arm × seed × attempt under fixed evaluation/search identities.
- **Artifact**: snapshot, backup, report, candidate collection or model bundle. Multiple snapshots are not multiple experiments.

Retain earlier pilots, stopped or invalid runs and manually reconstructed records. Do not call the earliest recovered pilot the first-ever experiment unless the source establishes that claim. Missing historical information is unknown, not zero. Link source documents and receipts rather than silently reconstructing details from names.

A proposed study has a question and rationale but no result. A registered study also has a versioned frozen protocol and planned arm/seed/budget identities. Registration is a separate explicit action. A proposed plan does not become registered merely because it has a TOML file or appears in the UI.

## Status is several fields

Keep plan maturity (`proposed`, `registered`), execution (`not_started`, `running`, `finished`, `aborted`, `failed`, `unknown`), acceptance (`not_reviewed`, `accepted`, `needs_review`) and storage receipts separate. “Finished” needs a completed run manifest and budget checks; “accepted” additionally links the validity/statistical report. An archived bundle is storage, not another execution status. Result conclusions such as inconclusive are scoped report fields, not a green success badge.

Status sources have precedence: scientific identity from registered protocol/configuration; execution from dated service/ledger receipts; acceptance from an immutable review record; file availability from separately timestamped verification. Never use an old TOML `status=planned` or pilot registry `status=running` as live proof. An old successful receipt becomes stale, not silently current. Present the latest verified state with its time.

## Page allocation

| place | role | content |
|---|---|---|
| Home | where we are | currently executing series, last accepted result, next proposed/registered item; links into the catalog |
| Arms | what is executing | current arm/seed workers and resource/health context; retained directories never count as running workers |
| Leaderboard Live | current search | feasible candidate rankings under one evaluation identity |
| Leaderboard Saved | research models | current/previous retained models, retention reasons, exact files and evaluation/backup availability |
| Past | experiment history and catalog | studies/stages/runs, recorded questions/results, technical routes, comparisons, reports and evidence |
| Hint | runtime suggestion | a typed, audited suggestion to a specific search; cannot register, approve or launch an experiment |
| Roadmap | future reasoning | why proposed work matters, dependencies and acceptance gates; links to proposed catalog entries |

Past defaults to **History** (anything that actually ran). A second **Planned** mode shows proposed/registered entries. **All** joins both with clear status tags. Expand study → stage → run; show independent historical pilots as their own groups. Search/filter by id, date, status, question, epoch or route. Do not add another top-level page or an independent planning database merely to show proposed entries.

A plan without results displays a question, changed factors, baseline, budget TBD where absent, dependencies and “not started”; it has no fabricated score, sparkline, chain verification or backup badge. A running series exposes operational progress by default. Comparative scores appear only at allowed protocol looks. A finished stage may be awaiting combined-study analysis; this must be legible for E43a/E43b.

## Comparison targets

Record a declared `comparison_parent` and comparison intent for a study, and a chronological predecessor separately. The previous dated experiment is not necessarily the scientific reference. For E43, compare arms with matched seeds in the combined study; do not present E43b versus E43a as an independent efficacy test. For a later protocol, show changed factors next to the previous relevant reference.

Build numeric comparisons only when evaluator/data/contract/metric/stage/cap, censoring and budget identities match and the required matching unit exists. Show paired effect/coverage and report link when a valid analysis exists. Otherwise show a side-by-side configuration diff with **different protocol** or **no matched comparison**. A route count is the number of observed declared route signatures, not number of arms, operators or successful trials. Leave it unknown until indexed.

## Data and retention contract

Use a versioned manifest per study and per stage, referring to immutable run receipts, reports, candidate collection ids and backup bundles by content hash. Retain plan revisions and explicit successor/predecessor links. Execution receipts append; regenerated search/list caches are replaceable. A migration must preserve unknown fields and old identities, and never rewrite frozen configurations to fake an updated status.

Use a compact JSON index for navigation; per-study/stage files hold details. Large histories are partitioned and paginated, with bounded file reads and lazy detail loading. Existing archive-index format 1 remains readable. Its current 2,000-row/2-MiB limits are implementation limits, not a scientific retention policy; the new catalog must partition before reaching them rather than delete older records or load all ledgers into the TUI.

A versioned export contains metadata, reports, exact source/environment/data recipe identities and selected artifacts; full private logs/raw data are opt-in with roles/licences respected. Export verifies hashes and writes a receipt; restore targets a new directory. Opening Past performs no extraction, training, deletion or SSH fetch. Keep machine paths in local location receipts; public manifests use stable artifact ids.

## Initial migration and acceptance

Seed the catalog from verified archives and recorded protocols. The known E43 study has two completed accepted stages and the combined final review; preserve their individual and combined reports. Historical pilots retain their own old identities and uncertainty. Future random/Optuna reference work and cap-3,000 follow-up can be proposed entries only after the actual question/dependency is written; this design does not register or start them.

Acceptance must exercise nested E43 stages, earlier pilot, failed/stopped run, operational snapshot, proposed plan without ledgers, stale execution receipt, missing artifact, invalid hash, many pages of history and incomparable protocols. All UI views must agree because they read the same catalog projection. No count of directories or archive files may become a count of active workers or scientific studies.
