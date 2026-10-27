# Development inventory and the scope of the E43 freeze

Checked October 27, 2026. Remote main is
`245b1f9e62a2cc91a6e34de759096f0132d07020`; the E43 continuation/documentation
branch is separate. This inventory also inspects the original local repository,
not just the deployment-maintenance checkout. Git fetch succeeded. GitHub API
authentication returned 401 during the preceding check, so Git refs establish
code availability, not the current open/closed state or hosted CI of every PR.

## Why work looked stalled

1. The maintenance checkout and remote main were being treated as the whole
   development inventory. Local `metrics`, `agent2`, `v063`, `llm` and `ops`
   branches contain additional history; some is new functionality, some already
   integrated code under different commit IDs, and some would regress main.
   “There is a branch” is neither “not started” nor “ready to merge.”
2. The October 20 request to review existing PRs without implementing new issue
   features was a bounded maintenance pass. Carrying that restriction into every
   later development decision incorrectly turned a temporary scope into a
   project-wide prohibition.
3. The October 17 section says nothing starts before E43 is read, while October
   19 explicitly supersedes that blanket rule and permits isolated development.
   Repeated dated tables still say branch-only/not released for features that
   have since shipped. The old restriction must not control new work.
4. There was no current acceptance queue linking each branch/feature to a test,
   integration boundary and next concrete action. Adding another priority table
   did not complete branch acceptance. E43's two occupied server CPUs explain
   why another long server study waits; they do not prevent local engineering.

## Existing implementations to accept before rebuilding

| work and roadmap link | evidence inspected | actual state and next action |
|---|---|---|
| Standard MSE/MAE display and per-dataset/horizon reporting (13) | local `metrics` head `19cb168`; `v063` head `c52436c`; `tiera.py`, `test_tiera.py`, `test_metrics_display.py` absent from main | existing implementation needs selective integration and review. It reports dev validation only, with the candidate's lookback and no bootstrap intervals/MASE; it is not the complete paper-comparable Tier A benchmark |
| Health-only series status and resource plan (#7) | `v063` includes `collect_status(..., values=False)`, `series plan`, `test_series_plan.py`; main status still calls the best-per-cap path | existing implementation to adapt to current CLI and guards. Default output should not read scores; test existing/nonempty ledgers and completion/error states, not just an empty directory |
| Agent v2 tools and recorded expectations (part of 35/41, support for 30) | local `agent2` head `6a603d7`, `v063` contains `check`, `neighbors`, `effects`, `predict`, `scorecard`, new arm keys and `test_agent_v2.py` | tool implementation exists; it is not a persistent PI or evidence that an agent helps. Review costs, epoch/arm/data scope, untrusted text and v1 identity; selectively integrate as new arms, then validate separately |
| Endpoint retry/budget/circuit breaker (39) | main already has health seeding, retries, budgets and schema-v2 support; comparison with local `llm` removes current deadline/reservation fixes and the endpoint underscore fix | predominantly already integrated, not a new feature backlog. Do not merge the old branch wholesale or infer lack of implementation from the dated roadmap row. Real endpoint reliability and research benefit remain separate validations |
| Seed runner/preflight/bundle/deploy (24/28 and operations) | main contains these modules and installed operations receipts exist; old `ops`/`v063` diffs also remove later protections | distinguish shipped capabilities, recovery already exercised, and outstanding drills/audit/scrubbing. Port only a demonstrated missing delta |
| Quieter Past header | remote `tui/quiet-past-header` head `cc595c8` changes six view lines plus a test and change note | already proposed code, not a new design task; review it with the archive-index fixes rather than redesigning the page again |

Full `v063` versus current main also removes `archive_index.py`, sealed-look
fixes and later tests, and drops `allow_with_arms` forwarding. These are concrete
reasons to avoid a wholesale merge, not reasons to postpone every useful part.
Keep the old local trees and uncommitted design notes intact.

## Work that can proceed while E43 runs

| lane | deliverable to develop/verify | acceptance and what still waits |
|---|---|---|
| Existing-branch integration | health/status/plan, then metrics report/display, then agent v2 tools as separate changes | current-main regressions preserved; focused and common suites; no server rollout or claims of empirical gain from passing mocks |
| Read-only research tools (21/22/31/41) | explicit two-stage report inputs; per-seed cost/anytime/evidence tables; lineage/neutrality diagnostics and tagged pooled views over old immutable pilots | identity and data-role isolation; no arbitrary off-policy claim for unseen actions. Do not inspect stage-2 outcomes to choose policy or stop |
| Research references (4/7/14/16/17) | same-runner random B/2B and well-configured conditional Optuna ask/tell harness; targeted ablation definitions; later same-task model references | toy/synthetic tests and protocol preparation now; independent long comparison only after E43 releases server slots. One initial reference, not every sampler at once |
| Reproducible model hand-off (24/26 and #8) | result manifest, one selected finalist export, preprocessing/spec/source/provenance and load-predict parity | preserve retained weights or label deterministic refitting; count all stored parameters. Latency/memory/quantisation measurements are separate from parameter count |
| Integrity and final claim (27/29) | mutable-table/event reconciliation on copies, then freeze/seal/claim CLI design and refusal tests | tests can use synthetic ledgers now. Real confirm/sealed data and counted looks require a separately approved frozen finalist/protocol; do not open them during E43 |
| Next evaluator/training (45–48) | golden replay; wider-dev-family and stage-redundancy design; fixed-lookback/per-task metrics; isolated gradient-finisher prototype with same-cap closed-form comparator | develop in a separate gym/profile, measure on toy or declared development data; no replacement of the installed E43 evaluator. New epoch and forward evidence before adoption |
| Runtime reliability issues | missing-cap defense, SQLite access, retention protection, containment and CI/data coverage | reproduce each defect; bounded fix and regression tests in its own change. Issue numbers and roadmap step numbers are separate namespaces |

The research sequence controls adoption and claims; it must not become a
serial lock on every implementation task. In particular, Tier A report tooling,
baseline harnesses, read-only pooled evidence and a separate gradient gym do
not logically require a positive screen result before development starts.

## Execution order

Begin by accepting the small existing health/status/plan change and metrics
report/display change onto current main in independent branches. Keep this
inventory and E43 results separate from runtime changes. Next accept agent-v2
tooling and the simple-reference harness; prepare the final report and finalist
manifest alongside them. Then open bounded audit/gate and next-gym work with
explicit acceptance tests. Do not put every roadmap feature into one release.

While E43 runs, use local toy/dev fixtures or old immutable pilot copies for
engineering. Additional training or model calls on the small server, changes
to the active evaluator/search policy, and new scientific claims are governed
by their own protocol and evidence. Completing E43 is one research decision,
not completion of the whole roadmap and not a prerequisite for all development.

## Acceptance receipt, later on October 27

Selective integration now includes series status/resource plans, standard metric
displays and development validation reports, agent-v2 tools through the actual
CLI/seed/definition entry points, and a small Past header trim. Existing
preflight, arm guards, sealed-look protections and archive functionality were
retained rather than replaced with the old integration branch.

Added best/latest leaderboard order and a separate candidate sidecar: immutable
configuration/evidence versions, scoped atomic best pointers and an explicitly
started watcher. An explicit train-only refit exports numeric weights,
preprocessing, source and provenance with cap enforcement and reload parity.
It never fabricates the historical weights that TinyTS did not originally save.

Validation: common suite **1174 passed, 28 skipped, 6 deselected**; slow chaos
suite **6 passed**. Initial local-branch auditing also exposed three tests that
assumed the repository working directory; those passed when rerun there. The
ported series-plan fixture now resolves its file relative to the test module.
Dataset-backed original Tier A checks passed; four such checks skip in the new
checkout without processed data. This is not completion of real-data CI #13.

Private integration smoke preserved 64 per-cap records from all 16 completed
E43a run copies. One selected development candidate was explicitly refitted on
ETTh1 training data at horizon 96. Its artifact stores 34 dense weight and
preprocessing values under cap 300; seven prediction rows reload with maximum
absolute difference 0. This validates artifact plumbing, not model superiority,
screen efficacy, confirmation or publication readiness. It does not read E43
stage-2 scores or use the server's worker slots.

These changes are not a package/server rollout. References #7 and #8 remain
open for their remaining acceptance work: full operational freshness/resource/
backup reporting and default-output compatibility review for #7; complete
finalist freeze/lineage/environment handoff and independent reproduction for #8.
Passing focused tests is not a reason to close the broader issues prematurely.
