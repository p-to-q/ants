# Glossary

The words ants uses, one meaning each. If code and this file disagree, fix one in the same change.

| term | meaning |
|---|---|
| **ants** | Autonomous Navigation of Tiny-model Space: the project, its command and its brand. The Python module is `antslab` and the package `ants-lab` because `ants` is taken on PyPI and npm. |
| **Gym** | an evaluator for one task domain: `evaluate(spec, stage)` → metrics. Fixed; proposers cannot edit it. |
| **Contract** | the TOML that fixes a gym's typed space, parameter caps, stages, budgets and data roles. |
| **Spec** | one candidate: a point in the contract's typed space. Data, never code. |
| **Space** | the typed grammar of specs. `Space.validate` is the only door for proposals. |
| **Trial** | one evaluation of one spec at one stage in one epoch. |
| **Stage** | a fidelity level of evaluation: S0 cheap, S1 rolling-origin, S2 full dev set. |
| **Epoch** | the identity of the measuring machinery: evaluator code hash, contract, data manifest. Never mixed. |
| **Cap** | a hard bound on the model's parameters. A constraint; also a bin of the frontier (100, 300, 1000, 3000). |
| **Stepping stone** | a deliberately kept over-cap candidate that may seed feasible descendants but is never a leader. |
| **Operator** | something that proposes specs (random, DE, QD, language model …). |
| **Portfolio** | the allocator that chooses operators by recent archive-improvement rate and lineage credit. |
| **Lineage** | the parent-child record of specs; used for credit and for path statistics. |
| **Ledger** | the SQLite source of truth: trials, events, artifacts, judgements, sealed looks. Hash-chained. |
| **Role** | what a dataset may be used for: `dev`, `confirm`, `sealed`, `stress`. |
| **Family** | an independent data source; correlated datasets share one and count once. |
| **Freeze / seal** | fixing a candidate's identity before its sealed look; the look is recorded against that hash. |
| **Registry ladder** | `candidate → confirmed → validated → champion`, with verdicts `accepted / unstable / rejected / failed`. |
| **Authority ladder** | `shadow → advisory → delegated`: how much a non-deterministic judge may decide. |
| **rel_mse** | geometric mean over datasets and horizons of MSE(model) / MSE(seasonal naive); below 1 beats the reference. |
| **Frontier** | the best error achieved at each cap; the object the work is about. |
| **Maintainer** | the optional, gated path by which a language model may propose a code patch to a gym. |
| **Hint** | free text from a human shown to proposers; it can never change a gate or a budget. |
