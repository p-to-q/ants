# Evaluation stack

ants evaluates at **eight layers**. Each layer answers exactly one question and emits exactly one kind of decision;
no layer's output is allowed to substitute for another's. Higher layers are slower, rarer, and more expensive.

| layer | question | decision it emits | authority | main code | status |
|---|---|---|---|---|---|
| E0 Integrity | Is this measurement valid? | admit / discard an observation | deterministic, fail-closed | `runner`, `ledger`, `campaign` (lock, epochs), `archive`, `janitor`, `data` (roles), `gate` | run on every trial; anchors and `gate` are not driven by any command |
| E1 Quality | How good is this model at this fidelity? | advance along the cascade (S0 -> S1 -> S2) | deterministic | `gyms/*`, `metrics`, `campaign` | run on every trial; proxy fidelity not checked |
| E2 Confirmation | Is the improvement real and general? | verdict: accepted / unstable / rejected / failed | deterministic statistics | `registry`, `metrics` | library only: nothing in production calls the registry, and `tinyts` emits no per-window losses |
| E3 Artifact | Does the thing work as software? | shippable or not | deterministic | `gyms/*` deep metrics | partly: robustness and cost accounting only; ONNX, latency, int8 bound not implemented |
| E4 Trajectory | Is this research *path* worth more budget? | fund / defund a path | statistics of the path's dynamics | `history.trajectory`, `landscape` | run (allocation only); predictive value untested |
| E5 Mechanism | Are our search operators and levers working? | re-allocate among operators / levers | bandit + lineage credit | `history.ops`, `portfolio` (`credit` is a report helper) | run; no equal-budget result yet |
| E6 Lab health | Is the lab progressing and is the evaluation machinery trustworthy? | continue / pivot / widen the domain / call a human | statistics + judges (not attached) | `ui.doctor`, `landscape.diagnostics`, `metrics.frontier_area` | partly: `ants doctor` runs; progress and judge calibration are not |
| E7 Claim | What may we assert outside the lab? | publish / claim | frozen protocol, sealed data | `ledger` (freeze, credits, anchors), `gate.seal`, `report` | library only: no command freezes, seals or reports |

Status words are explained in [surfaces](../surfaces.md); "library only" means covered by tests, not reachable from `ants run` or the CLI.

Cross-cutting rule, the **authority ladder**: a non-deterministic judge (an LLM, a System-One model such as Jev) starts in
*shadow* (logged, not acted on), is promoted to *advisory* and later *delegated* per decision type only when its logged
predictions are measured against ledger ground truth (E6) and meet calibration targets. It can never override E0 or E7; today that holds because no judge is attached to the loop (`ProposalTriage` can only reorder proposals).

Read the per-layer files in this directory. Metric definitions live in `src/antslab/metrics.py`.
