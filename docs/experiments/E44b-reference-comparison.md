# E44b: fixed-budget search reference comparison

Registration: this protocol and `deploy/series/e44b.toml` are frozen in the tested source commit before the first worker starts. An exclusive `registration.json` beside `seeds.json` records the source archive hash/commit, protocol/contract hashes, epoch, exact arm definitions and runtime versions. No result-dependent changes, efficacy peeks, seed extensions or early superiority tests.

## Question and intervention

Does unchanged `portfolio` earn its complexity against a maintained conditional Optuna reference and pure random search? The factor is proposal/search strategy. No LLM, Jev, warm start, migration, neural evaluator or screening adoption is added. `portfolio_optuna` is an available independent implementation path, not an E44b arm.

All arms use one new evidence-enabled TinyTS contract. It reuses the TinyTS model/scoring primitives, fixes normalization to each fold's training prefix, records per-dataset/horizon/fold receipts, compressed per-window losses and original S2 fitted states, and protects verified inventories. The strict profile omits the old secondary robustness/seasonal diagnostics rather than treating them as an objective. Its epoch is independent of the closed E43/E44a epochs. Capture-only legacy mode must reproduce the original numerical scores exactly (excluding timing/added diagnostics). The strict profile has its own fixed objective identity and must pass a future-value perturbation test: changing values after a fold boundary cannot change that fold's fitted weights or preprocessing. Strict-profile scores are not pooled with E43/E44a. Original E43/E44a files remain unchanged.

## Frozen design

- Arms: `portfolio`, `tpe_reference`, `random`; independent workdirs, no cross-arm history or weights.
- Paired independent seeds: **201–208**, eight pairs, disjoint from E43 and E44a.
- Budget B: **7,200 charged evaluation CPU-seconds** for portfolio and Optuna; random runs uninterrupted to **14,400**. Derive random-B from that same run's prefix; do not pay for a duplicate random-B run.
- Total: 24 workdirs, 64 evaluation CPU-hours plus controller overhead and final crossing trials. Two workers, single-threaded BLAS, existing 180% CPU / 1,500 MiB service ceiling. Expect roughly 36–48 wall hours conditional on measured capacity; record actual start before giving a finish estimate.
- Primary cap: **≤300 nominal parameters**, preserving the earlier tiny-model deployment question, not chosen from whichever E44a bin favoured an arm. Every method searches the same ≤300 feasible region; no reference is asked to optimize 3,000-parameter models and then judged only at 300. Other caps are deferred to a separately frozen study. Actual stored weights/preprocessing counts are independently checked; a nominally feasible model is not automatically deployable.
- Primary endpoint: best completed S2 `rel_mse` within cap whose finish-ordered cumulative charged evaluation CPU is ≤B. Include every terminal outcome in CPU accounting; exclude the crossing trial from endpoint selection while retaining it in evidence. Use the same rule at random-2B.
- Optuna 5.0.0, startup 10, multivariate/group/constant-liar enabled, **S1-only objective feedback**, at most the latest 1,500 terminal objective observations by ledger trial ID. Active conditional dimensions follow `Space.when`; inactive defaults stay outside sampler observations. First-stage/S1 pending jobs and durable unsubmitted proposal intents are represented as pending. No s0/s1/s2 score pooling. Settings/source hash are in the arm definition.

B is a bounded increase over the completed one-hour E44a calibration, allowing the reference to move beyond startup and observe more than a handful of S1 evaluations without another week-long campaign. Eight seeds permit an exact paired diagnostic at this hardware budget; they are not advertised as achieving a pre-established power for a 1% effect.

## Analysis and decision, once after all budgets complete

Two primary comparisons: portfolio-B versus Optuna-B; portfolio-B versus random-2B. For each seed use log(error ratio), negative favouring portfolio. Report all eight values, median ratio and exact two-sided signed-rank sign-enumeration p-values (drop exact zeros, average tied absolute ranks), with Holm correction over these two tests at family α=0.05. A practical benefit requires median ratio ≤0.99 in each comparison. Adopt portfolio as search strategy only if both practical and corrected statistical gates pass; otherwise retain the simpler reference / report inconclusive. Do not interpret failure to pass as equivalence.

Pure random-B and Optuna versus random-B are descriptive checks, not extra primary adoption tests. Report cap coverage, actual budget overrun, failures/timeouts, S2 counts, end-to-end resource observations, proposal overhead, retained checkpoints and physical-cap rejections. Never count overlapping windows as independent seed replicates.

If any registered seed lacks a finite eligible endpoint, do not drop or impute it: report incomplete evidence and make no superiority adoption. Infrastructure interruptions may resume the exact workdir and retained CPU budget; preserve all attempts. Repair that changes a proposal/evaluator identity ends the affected comparison and needs a new registered study. No live tuning based on comparison scores. Health/status checks remain permitted.

## Artifact and reliability gates

Before launch: common/chaos CI; conditional and restart/failure tests; original/checkpoint parity; scoped history/definition checks; target-host proposal CPU/RSS and a bounded evaluation smoke; off-host backup of earlier studies. Per-call overhead telemetry is diagnostic and must be reported alongside the charged budget. Equal charged CPU is not a claim of equal end-to-end cost; no production-efficiency claim is allowed without total-resource accounting.

S2 checkpoints are captured during evaluation, not silently refitted. Each dataset/horizon/fold records train/validation bounds, sampling identities, physical count, source and load/predict parity. An over-cap or parity-failing export is explicitly rejected, not labelled available; task scores are retained. Candidate models are not release-ready solely because search improves. Finalists require complete applicable coverage and physically feasible bundles before any deployment claim. Confirmation/sealed roles are not opened by this series; a later frozen-finalist confirmation needs a separate finite-look protocol.

An independent synthetic neural gym is engineering-only and not part of these arms or endpoints.
