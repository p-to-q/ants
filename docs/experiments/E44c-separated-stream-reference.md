# E44c: independent-stream reference repair

## Reason and scope

E44b completed all 24 budgets. Its frozen script returns no portfolio adoption: median portfolio/Optuna-B error ratio 1.0499302 and portfolio/random-2B ratio 1.0239470. The Optuna endpoint is identical in all eight runs. The audit found `_sampler(seed + ordinal)` produces shifted, overlapping startup streams for adjacent master seeds. Distinct process seeds and valid ledgers do not establish independent reference replicates. Keep the frozen nominal p-values as historical output, disclose the dependence, and do not claim independently replicated superiority from that reference contrast. No E44b seed extension or post-hoc pooling.

E44c repairs this implementation defect under a new arm identity, `tpe_reference_v2`. It derives each uint32 sampler seed from SHA-256 of a domain, master seed and ordinal. Original adapter source and old arm remain unchanged. Hashing removes the demonstrated systematic stream overlap; it is not a proof of all statistical independence. No sampler hyperparameter is tuned against E44b scores.

## Frozen experiment

- Same strict fold-prefix TinyTS evaluator, development datasets, nominal cap <=300, promotion, initialisation and charged CPU accounting as E44b; evaluator epoch d0e8164662d7.
- Arms: unchanged portfolio, tpe_reference_v2, unchanged random. No shared history, warm start, LLM, Jev, screening, neural model or checkpoint reuse.
- Eight fresh paired search seeds 301–308. Portfolio/reference B=7,200 charged evaluation CPU-s each. Random uninterrupted 2B=14,400; B derived from the same prefix. 24 workdirs / 64 evaluation CPU-hours plus crossing and controller cost; two workers, 180% CPU quota, 1,500 MiB service memory limit. Expected roughly 36–48 hours after actual launch; capacity conditional.
- Best completed S2 rel_mse at nominal cap <=300 with finish-ordered cumulative terminal CPU <=B (random <=2B for primary), excluding crossing trial from selection. Charge all terminal failures. Physical checkpoint feasibility remains a separate acceptance gate.
- Frozen comparisons: portfolio-B versus reference-B and random-2B, eight log(error ratios), exact two-sided paired signed-rank sign enumeration, tied average absolute ranks, exact zeros dropped; Holm over two tests at family alpha .05. Portfolio adoption requires both corrected p<=.05 and both median ratios<=.99. Failure is not equivalence. This is a new development comparison; E44b data is not pooled or used to add seeds.
- Report every seed, random-B descriptively, per-arm failures, S2/task coverage, checkpoint rejections, CPU crossing, end-to-end controller CPU/RSS/wall observations. Windows/folds are not independent replicates.
- Missing finite endpoints or unfinished budget: incomplete evidence, no adoption, no imputation. Identity-changing repair ends comparison and needs a new registration. Resume only exact retained workdir/identity/budget. No efficacy peeks, seed extensions or tuning during execution. Health checks are permitted.

## Admission and registration

Before first scientific workdir: old final ledgers and full artifacts archived off-host and verified, complete artifact hash/coverage audit, finalist independent reload; stream separation/recovery tests and common/chaos regressions; fresh target-host V2 smoke and actual S1-conditioned proposal CPU/RSS admission; runtime epoch/data manifest and unchanged historical arm hashes verified. Source archive, protocol, series, contract, analysis/registration scripts and exact arm hashes are recorded in immutable root-owned registration, copied off-host before launch. Runtime uses a separate source directory; old runtime and E44b files remain available.

No confirm/sealed dataset is opened. No result is a deployment or publication claim. Physically rejected models remain rejected even if they win the nominal score. A future real-data neural-gym study is independent work and is not blocked conceptually by this repaired reference comparison; it needs its own evaluator and training/export acceptance rather than mixing factors here.
