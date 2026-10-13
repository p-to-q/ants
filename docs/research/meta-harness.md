# Meta-Harness, and ants read through it

Reading notes, 2026-10-13. Facts about the paper come from its PDF (arXiv 2603.28052 v1, 30 Mar 2026) and the official repository; facts about neighbouring work come from the PDFs of those papers read one by one. Where a source could not be read or a number could not be checked, the line says so. Tool-made summaries of these papers were wrong in places, so none of them is used here.

ants is a meta-harness in the paper's own sense: a harness that decides what a proposer sees and what an evaluator decides, around a model it searches over. The paper does it for prompts, retrieval and agent scaffolds; ants does it for tiny forecasting models. This note records what the paper shows, what later work qualifies, and what ants takes from it.

## 1. The paper

Lee, Nair, Zhang, Lee, Khattab, Finn (Stanford, KRAFTON, MIT). Project page and demo: yoonholee.com/meta-harness. Code: stanford-iris-lab/meta-harness (MIT). Optimised Terminal-Bench harness: stanford-iris-lab/meta-harness-tbench2-artifact.

| | |
|---|---|
| Object searched | a harness: a stateful program around a frozen model that decides what to store, retrieve and show (single-file Python, 100 to 1000 lines) |
| Objective | maximise expected final reward; with two objectives (accuracy, context cost) report the Pareto frontier |
| Loop (Alg. 1) | start from a population of valid harnesses; each iteration the proposer inspects the filesystem, proposes k new harnesses, those that pass an interface check are evaluated and stored |
| Proposer | a coding agent (Claude Code, Opus 4.6) with grep, cat and file edits, steered by a short skill text |
| Experience | one directory per candidate: source, scores, execution traces; the proposer reads what it wants |
| Selection | none: no parent rule, the proposer may inspect any candidate; the final test runs on the Pareto frontier |
| Held out | the proposer never sees test results; it sees only the search set |
| Budget | about 60 harnesses over 20 iterations typically; runs finish in a few hours |

The claim: optimisers that compress history (a scalar, a summary, a window) cannot trace a late failure to an early design decision; giving the proposer the raw history through a filesystem can. Table 1 of the paper puts the context per step at 0.002 to 0.026 MTok for OPRO, TextGrad, AlphaEvolve, GEPA, Feedback Descent and TTT-Discover against 10 MTok. The figures are the authors' estimates.

### Evidence

| Result | Numbers |
|---|---|
| Ablation of what the proposer sees (text classification, Table 3) | scores only: median 34.6, best 41.3; scores plus an LLM summary: 34.9, 38.7; full traces: 50.0, 56.7 |
| Against text optimisers, search set (Table 4) | GEPA 32.6 / 40.2; best-of-N 34.0 / 44.2; OpenEvolve 39.1 / 43.3; TTT-Discover 34.1 / 45.6; Meta-Harness 50.0 / 56.7 (median / best) |
| Text classification, test (Table 2) | 48.6 against ACE 40.9 and MCE 40.0, with 11.4K context characters against 50.8K and 28.5K; nine unseen datasets 73.1 against ACE 70.2 |
| Retrieval-augmented maths (Table 6) | one harness, five held-out models, 34.1 to 38.8 (+4.7); BM25 +3.4, dense retrieval (k=5) +4.0 |
| Terminal-Bench 2 (Table 7) | Opus 4.6: 76.4 (rank 2 of the reported; one higher entry, 81.8, they could not reproduce); Haiku 4.5: 37.6, rank 1 (next 35.5) |

How the proposer works (Appendix A, ten iterations on Terminal-Bench 2): it reads a median of 82 files per iteration (range 69 to 99): 41% harness source, 40% execution traces, 6% score files, 13% other. In the logged run, iterations 1 and 2 bundled structural fixes with prompt edits and both regressed; iteration 3 named the shared prompt change as the confound; after six regressions iteration 7 switched to a purely additive change (an environment snapshot gathered before the first model call, about 80 lines, which improved 7 of 89 tasks), iteration 8 composed it with an earlier fix, iteration 10 reused a lesson from another run. The paper reads this as causal reasoning over failures that compressed feedback cannot support.

### What the code adds

From reading the repository, not the paper:

- The outer loop is ordinary Python. Per iteration it deletes the old hand-off file, calls the proposer, import-checks each candidate, runs the benchmark outside the proposer, rebuilds the frontier and appends one line per candidate (hypothesis, axis, components, score delta) to `evolution_summary.jsonl`.
- The skill is where the discipline lives. Text classification: exactly three candidates per iteration, mixing exploitation and exploration; parameter variants of an existing system are called the most common failure and "almost always regress or tie"; every candidate must change a mechanism; prototype on logged examples before writing the system; no dataset names in candidate code; do everything in the main session because constraints get lost when delegated. Terminal-Bench: one candidate per iteration, one mechanism, mechanism first, no task names, copy an existing agent rather than importing another candidate.
- The test split is separate in the filesystem and a run is frozen by a status file (`finalized.json`) before its single test evaluation; evolving a finalised run is refused. A test asserts that a default run never calls the test path.
- The experimental Harbor pilot is the stricter version: the outer agent gets a bounded `evaluate_harness` tool with an evaluation budget and an append-only history; every task lists strings the harness may not reference, checked on the source before scoring; the generated code is never imported by the long-lived controller, only staged and run by short-lived child processes; the source SHA-256 is recorded with each score. The README says the repository is a cleaned-up version "not tested beyond verifying that it runs".

Appendix D lists what the authors found to matter, and ants follows nearly all of it: write a good skill (constrain outputs and safety, not the diagnosis procedure; debug it with short 3 to 5 iteration runs; accumulated traces shape behaviour more than the skill); start from a baseline and a search set that is hard for it, small enough for about 50 full evaluations; log everything in a navigable format (JSON, hierarchy, regex-friendly names); put a small CLI over the logs (frontier, top-k, diff between runs); validate cheaply before expensive benchmarks; run the evaluation outside the proposer.

## 2. Neighbours, read in the original

| Method | Object | What the optimiser sees | Selection | Note |
|---|---|---|---|---|
| OPRO 2309.03409 | prompt | top-20 (instruction, score) pairs, no failure traces | keep best | authors: error cases were not used effectively; reports best-by-test instruction |
| ProTeGi 2305.03495 | prompt | the prompt and 4 sampled errors | beam, bandit | test F1 peaks near 3 steps then overfits |
| TextGrad 2406.07496 | any text variable | an LLM critique of the current value | accept if validation improves | stability left open |
| GEPA 2507.19457 | prompts of a compound system | parent prompt plus raw traces and feedback for 3 rollouts | Pareto sampling | uses raw rollouts; labelling it "summary" is a simplification |
| ACE 2510.04618 | context playbook | reflector and curator deltas, batch size 1 | incremental merge | argues against compression; needs a reliable signal |
| MCE 2601.21557 | skills and context files | a database of prior skills with scores; raw rollouts through files | (1+1) with validation | closest design to Meta-Harness; ablation gaps small |
| Feedback Descent 2511.07919 | text artifact | current best and pairwise rationales since last accept | (1+1) | true vs random feedback separates (9.91 vs 7.81 on DOCKSTRING) |
| AlphaEvolve 2506.13131, OpenEvolve | programs | sampled programs, scores, evaluator output | MAP-Elites plus islands | evaluator is user code outside the model; cascade of test stages |
| FunSearch (Nature 2024) | one function in a skeleton | 2 programs from one island | island reset | invalid or over-limit programs discarded |
| ADAS 2408.08435, AFlow 2410.10762 | agent code, workflow code | archive of all prior agents; tree-path logs | none (ADAS); soft tree search (AFlow) | AFlow's re-run puts ADAS below plain IO (67.2 vs 72.8) |
| MemEvolve 2512.18746, ALMA 2602.07755 | memory designs | per-trajectory diagnoses; sampled design plus logs | Pareto top-K; weighted sampling | ALMA: greedy parent 77.1 vs 87.1 on ALFWorld |
| Darwin Gödel Machine 2505.22954, Huxley-Gödel Machine 2510.21614 | the agent's own code | its benchmark logs | open archive, every agent stays reachable | DGM: greedy parent 39.7 vs 50.0 (SWE-bench) with archive; HGM's parent score correlates 0.778 with clade productivity vs 0.285 for DGM |
| STOP 2310.02304 | the improver program | its own code and a utility description | none | budgets given only in prompts were ignored by selected programs |
| TTT-Discover 2601.16175 | a solution, and the model's weights | one reused earlier state | PUCT | needs a continuous reward and a verifier; best-of-N baseline already close on one task |

What the comparison shows. Raw traces are not new: GEPA, TextGrad, ProTeGi and MCE use them at smaller scope. What is distinctive is reading all candidates at once and leaving selection to the proposer. The only ablation that isolates raw traces from a summary of the same traces is Meta-Harness's own, on one task (Table 3). Token budgets in the comparison are the authors' estimates.

## 3. What later work qualifies

| Finding | Source |
|---|---|
| The final harness of unmodified Meta-Harness restated the grading rubric of its evolve suite in a 730-word method without naming a task, so a regex audit passes it. With the proposer's edits capped per candidate (cosine-annealed), a history of every edit with its measured effect, a leakage critic that reads the diff before scoring, a noise floor measured by re-running the base, and pruning, held-out gains rise: out-of-distribution average 43.6 against about 40.6 (base 39.7), in-distribution held-out equal at 89.2 | RRSI 2609.24972 (Table 1, Table 9) |
| Harness search is domain dependent: it reaches peak weight-only accuracy with 5.8% of the rollouts on one task and 0.4% on another; a 60-iteration search keeps raising training score and gains +0.21 on test; alternating with fine-tuning beats staged | WHALE 2609.00196 |
| Selecting by development score loses 3.0 to 3.5 points against the test-best of the same top ten; two searches plus a router explain about 5 of a 16-point gain | Mixture of Self-Improving Branches 2609.37834 |
| A train/validation split in which the proposer never sees validation scores, with a beam ranked on validation, helps; the baseline comparison there is confounded (5 iterations, no split) | AutoRef 2609.35530 |
| Without labels: a determinantal-point-process coreset, failure diagnosis by self-consistency, and acceptance by preference over old trajectories reaches 0.78 with 103 agent calls against 0.80 for 10 Meta-Harness rounds with 320 | RHO 2606.05922 |
| Accept an edit only if the training score improves and a hidden development score does not decline; one edit per component | AutoDesign 2608.13560 |
| The Terminal-Bench 2 search and its final evaluation use the same 89 tasks; leakage was checked by regex and by hand | the paper's §4.3 |
| A selected agent switched off the logging channel a hidden hallucination detector read, and scored 2.0; hacking was more frequent when the detector was not hidden | DGM 2505.22954, App. H |
| Constraints stated only in the proposer's prompt were dropped by the programs that were selected; a sandbox flag was turned off in 0.42% of 10 000 attempts even with a warning | STOP 2310.02304, §6 |
| About 57% train/test overlap and misreported figures in an accepted-then-withdrawn paper, found by the authors' own audit | AI Scientist-v2 2504.08066 |

Practice sources agree on the shape of the harness around an agent: a fixed, outcome-based evaluator that checks final state, with a known-good case that must pass and a no-op that must fail (Terminal-Bench 2, 2601.11868); one narrow editable surface, a fixed time budget, a metric file the agent must not edit, keep or discard by a single number (Karpathy's `autoresearch`, whose read-only rule is prose); cheap deterministic checks on every change and costly judges later (Böckeler, martinfowler.com); error messages that tell the agent how to fix the problem (OpenAI, "Harness engineering"). Not verified: the live text of the OpenAI post (403; a later snapshot was read), the second ForgeCode post, any leaderboard entry.

Not found in anything read: a measurement of how unmodified Meta-Harness degrades with more iterations, cost in dollars of the paper's own runs, or any study with a proposer other than Claude Code.

## 4. ants as a meta-harness

Today the LLM proposer sees the top 8 specs with one score each, a few verified lessons from `dream` (summaries of the ledger, re-checked by statistics) and up to 3 human hints. That is the OPRO regime of the table above, with a summary layer: the setting in which the paper's ablation found no gain from summaries. ants already holds what the paper calls the filesystem: every trial's spec, per-stage metrics, logs and lineage sit in the hash-chained ledger and the per-trial directories, and `ants sql`, `show`, `best`, `log` are the small CLI Appendix D recommends. What it lacks is a proposer that reads them.

What ants has that the paper's setups do not: the proposer is never in the evaluation path (roles `dev`/`confirm`/`sealed`, a frozen evaluator epoch, fencing tokens), a cascade of cheap to expensive stages, a hash chain that makes the experience tamper-evident, and sealed looks that are counted. The follow-up literature above is largely a list of problems these already address.

| Layer | Searched | Today | Meta-Harness move | Status |
|---|---|---|---|---|
| Model | spec of a tiny forecaster | portfolio of operators; scalar history | an agent proposer that reads the ledger through read-only tools (`sql`, `show`, `diff`, `best`) and proposes specs only | `agent` operator and `portfolio_agent` arm (`src/antslab/agent.py`) |
| Evidence | what the proposer may read | top 8 plus lessons | a navigable export of the ledger with an index, a frontier and a skill file | `ants workspace` |
| Audit | which evidence led to which proposal | the LLM call is logged, its inputs are not | every read the agent makes is a chained ledger event, so selection that is "whatever grep surfaced" is visible | with the agent operator |
| Hypotheses | why a candidate was tried | `rationale` string per call, 160 characters | per proposal: hypothesis, axis (mechanism or parameter), parent; analysis by `ants sql` | proposal metadata |
| Proposer code | the operators themselves | hand-written; `maintainer` patch gate not wired | search over operator code scored by replaying the ledger | recorded, not built |
| Cascade policy | stage budgets, promotion quantile, proxy | set by hand from smoke runs | a search over the contract's cascade with accuracy-per-compute as the objective, on the same ledger | recorded, not built |
| Decisions | promotion, stopping, which arm gets compute | fixed rules, shadow judge | the same read-the-traces proposer applied to promotion proposals, still gated | recorded |
| Joint | harness and weights | fixed gym | alternate operator search with model fitting (WHALE's pattern) | recorded |

Principles taken, each with how ants enforces it:

1. The agent proposes; a fixed evaluator it cannot reach decides (Appendix D, Harbor pilot, Terminal-Bench 2). Enforced by the role firewall, the epoch, and the proposer interface: an agent operator returns specs, never code or scores.
2. Experience is a filesystem, read raw, selectively (the paper). Enforced by a read-only SQL connection with an authorizer, size and time caps (`sqlview`), and a workspace export.
3. Whatever the agent reads is recorded (Meta-Harness leaves selection to the proposer; DGM and ALMA show silent greedy drift). Enforced by one hash-chained event per tool call, with the query text and the size of the answer.
4. Constraints live outside the proposer's prompt (STOP, DGM). Enforced in code: the space validator, the feasibility prefilter, per-day call cap, per-call tool cap.
5. Mechanism before parameter (the skill rule: parameter variants "regress or tie"). The agent prompt asks for a hypothesis and an axis; `portfolio` already holds the parameter-level operators, so the agent arm is asked for structural changes.
6. A hidden gate against fitting the search set (RRSI, AutoDesign, MoSIB). ants's `confirm` and `sealed` roles are that gate; the agent arm never sees them.
7. Compare at equal budget (TTT-Discover's best-of-N, AlphaEvolve ablation). The experiment below holds LLM calls and evaluations equal.

## 5. Hypotheses, and the experiment that decides them

| Id | Claim | Would be refuted by |
|---|---|---|
| MH1 | An agent that reads the ledger finds better specs per evaluation than the same model given the top 8 and a summary | `portfolio_agent` not above `portfolio_llm` at equal evaluations and equal LLM calls, over 3 or more seeds |
| MH2 | The agent's gain comes from reading failures, not from extra context | an arm given the same tokens of random ledger rows doing as well |
| MH3 | Mechanism-level proposals beat parameter-level ones once the portfolio has converged | no difference between the agent arm with and without the "structural only" instruction |
| MH4 | An agent that sees only dev scores overfits the dev datasets no more than `portfolio` does | a larger dev-to-confirm gap for the agent arm at equal dev score (confirm data is read only by the gate) |
| MH5 | Replaying the ledger is a cheap fitness for proposer code | replay ranking of operators disagreeing with their live ranking |

The server runs `portfolio`, `portfolio_llm` and `portfolio_agent` from 2026-10-13, one instance each (a third of two cores). The agent operator passed its tests and one real batch against a copy of a server ledger (6 tool calls, 6 valid proposals, about two minutes). Nothing here is a result.

## 6. Not decided

- Whether the agent should be allowed to propose changes to the contract's cascade. It would move the evaluator, which is the one thing the design keeps fixed; a separate, human-approved lane (new epoch) is the only version consistent with the principles.
- Dollar cost per proposal of a tool loop. The per-day call cap bounds it; the real cost is unmeasured.
- Whether the proposer should be a coding agent with a shell, as in the paper. ants uses a closed set of tools instead; that costs flexibility and removes a class of risks. Revisit only with a sandbox that makes the evaluator physically unreachable, not unreachable by instruction.
