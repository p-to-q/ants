# E44a engineering acceptance — November 3, 2026

Execution is complete; artifact acceptance is **partial / needs review**, not a scientific efficacy result. Server readback at 00:21–00:29 Asia/Shanghai: all six runs reached 3,600 charged CPU-seconds, service inactive with PID 0 and NRestarts 0. All six epoch/definition/chain checks passed. The original registered epoch remains `5da6eeee6527`.

The complete six-ledger bundle was sealed on the server, downloaded and independently verified on the owner's Mac. SHA-256: `b323eb6d2d0f3967048804b4f754757328203f4a0c7c50a8c20085b35c0361ea`. A separate full-workdir archive was also downloaded; all **1,028 registered metric/log files** matched their ledger SHA-256 values with no missing files or mismatches. Raw ledgers and weights are private; this document is not a public rerun package.

## Budget-scoped development diagnostics

Values below use deepest-stage completed trials whose finish-ordered cumulative charged CPU (including unsuccessful terminal outcomes) is at most 3,600. They exclude the completed budget-crossing trial. These are descriptive within-run minima, not confirm results or hypothesis tests. Queued work left when a budget is reached is retained evidence, not a failed evaluation.

| run | finished | queued | charged CPU-s | ≤300 dev rel_mse | ≤3000 dev rel_mse |
|---|---:|---:|---:|---:|---:|
| portfolio_s101 | 317 | 3 | 3641.42 | 0.845583 | 0.812075 |
| portfolio_s102 | 301 | 1 | 3600.86 | 0.910786 | 0.805202 |
| random_s101 | 152 | 0 | 3622.61 | 0.928847 | 0.798014 |
| random_s102 | 203 | 1 | 3601.40 | 0.878585 | 0.816608 |
| tpe_s101 | 294 | 1 | 3605.90 | 0.900739 | 0.826988 |
| tpe_s102 | 181 | 2 | 3653.28 | 0.891912 | 0.809643 |

All 1,448 evaluations finished successfully; eight queued entries remain across the six ledgers. All six have zero recorded LLM calls and zero unseals. The 100-parameter cap has no qualifying budget-scoped S2 observation for `tpe_s102`; missing coverage is not zero performance. Two seeds and an engineering-calibration protocol do not establish search superiority. No statistical adoption decision is made.

## Model handoff checks

Immutable candidate specifications were preserved independently for each run/cap using the existing candidate store. Four pooled development-selected candidate variants were newly refitted on **etth1 training data, horizon 96**, exported with preprocessing/source/checksums and reloaded. These are new fits, not historical evaluation weights. All four physical-cap checks passed. Each saved-weight validation uses 210 channel/windows from the development validation split, with no confirm or sealed access.

| nominal cap | saved-weight dev rel_mse | status |
|---|---:|---|
| 100 | 0.985678 | saved, reloaded, development-only |
| 300 | 0.944694 | saved, reloaded, development-only |
| 1000 | 0.817076 | saved, reloaded, development-only |
| 3000 | 0.850108 | saved, reloaded, development-only |

The source ledger metrics do not include complete per-dataset/horizon/fold receipts, and registered artifact kinds are metrics/logs rather than fitted-state checkpoints. Therefore full coverage acceptance and historical-weight recovery have **not** passed. The four etth1/h96 checks are scoped engineering checks and cannot be substituted for the original multi-dataset S2 scores. An initial local attempt used the display name `ETTh1` instead of catalog id `etth1`; the role guard correctly rejected it before data loading. That failed receipt is retained separately.

## Decision

Close execution; preserve all evidence and do not append seeds. Do not adopt a search operator from these diagnostics. Proceed with offline Optuna-reference engineering and the isolated neural-training prototype. Before another model-producing rollout, implement evaluation-time checkpoint receipts and complete physical-cap/coverage acceptance. [Next work and E44b gates](../research/e44-next-work.md). E44b is not registered or started by this report.
