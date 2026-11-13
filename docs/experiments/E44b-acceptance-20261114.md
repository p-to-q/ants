# E44b acceptance — November 14, 2026

Execution and retained-artifact acceptance passed, with a scientific limitation in the original Optuna reference. No portfolio adoption. No confirmation, sealed-data, model-deployment or generalization claim.

## Execution and frozen analysis

All 24 registered runs reached their charged CPU budgets. The service exited successfully with no restart. First event: November 3, 02:33:26 Asia/Shanghai; final event: November 4, 16:18:30. The 27,929 completed trial rows include 3,056 S2 evaluations; no failed, timeout or crashed terminal rows. Queued work left at the budget stop remains recorded, not counted as completed or silently evaluated. Charged evaluation CPU: 230,743.97 seconds (64.096 hours), including crossing trials; the registered endpoints exclude the crossing trial.

All ledger integrity checks, hash chains, evaluator epoch `d0e8164662d7` and frozen arm definitions match. No LLM calls or unseals. The final compressed ledger bundle SHA-256 is `f859bdc1adcebc2b426e8ea95939ea595c4ba11de66c74b794af48b5e8347caa`. Its 24 raw ledger hashes remain identical after the read-only local analysis.

The frozen script returns `no_portfolio_adoption`. Its reported error-ratio statistic is exp(median(log paired error ratio)). Portfolio/Optuna-B: 1.0499302326, nominal Holm p=0.015625. Portfolio/random-2B: 1.0239469776, Holm p=0.0234375. These results do not pass the portfolio adoption gates.

## Reference replication defect

The eight original Optuna runs all selected key `aafc22cf61d7e32d`, rel_mse=0.8092051781. Audit of actual proposal journals found that `_sampler(master_seed + ordinal)` makes adjacent master seeds reuse shifted startup random streams: seed 201's second initial proposal equals seed 202's first, and so on. Separate workdirs and distinct master-seed labels therefore do not establish independent reference replicates. Keep the nominal frozen-script output, disclose this dependence, and do not present that reference contrast as independently replicated significance. This does not support portfolio adoption, equivalence or a retroactive seed extension.

The repaired `tpe_reference_v2` has a new identity and domain-separated hashed proposal seeds. [E44c](E44c-separated-stream-reference.md) uses new seeds and no E44b pooling. No sampler setting was tuned against these scores. The original adapter remains unchanged for historical identity/replay.

## Complete registered evidence, not refits

The independently verified lossless content-addressed backup retains all 794,467 registered paths from 286,335 verified content blobs, plus the sealed ledgers, original registration/seeds and full per-run logs. Logical registered size is 6,934,139,800 bytes; deduplicated content is 2,742,748,582 bytes. Archive SHA-256: `04606b774a7f4e889041645095ce6ae94c55cf97ac2f8de7a5de1b6240898fce`. Zero missing references or hash mismatches; 788,355 protected file registrations. Every original path is reconstructable from the sealed ledger-to-blob mapping. Unregistered scratch files remain in the original closed server workdirs; they are not claimed as part of this off-host registered-evidence archive.

All 3,056 S2 evaluations cover 48 applicable tasks each: 146,688 dataset/horizon/fold task receipts, training-prefix boundaries and inventory anchors verified. Of these, 134,433 task checkpoints were accepted and 12,255 were explicitly rejected because actual dense weights plus preprocessing exceed cap 300. Rejected exports are not usable models. Checkpoint counts are not independent configurations or statistical replicates.

The 24 budget-eligible run finalists contain 1,011 accepted task packages. Each loads successfully. A fresh child process for every run then imports only that finalist's archived Python source and reproduces the installed reference's exact predictions on deterministic mean/scale-derived probes. This is original evaluation state, not a newly fitted replacement; it is not a new held-out quality evaluation. Original scoring-parity receipts are retained and hash-verified.

All 17 distinct finalist specifications are retained, with their accepted/rejected task counts. They all use a period core; do not invent architectural diversity from the count. The best nominal score has 48/48 accepted task exports, but its Exchange errors exceed the naive reference at all four horizons (about 1.020–1.126). A favorable aggregate development score is not a per-family or publication claim.

## Repair validation and next runtime

Local common regression: 1,252 passed, 28 skipped. Five chaos tests passed; the concurrent-ledger throughput case initially completed only 15 trials against its >20 threshold while suites were running together, with integrity/chain checks passing. It passed unchanged in isolation in 23.13 seconds. Both receipts are retained; the initial failure is not counted as a pass. GitHub Actions did not produce a run for this PR at the time checked; new cross-platform CI is not claimed.

Target-host V2 smoke: two independent workdirs, eight completed trials each, 192 S2 tasks in total, no terminal failures, chain/inventory checks pass, no shifted initial proposal stream. Peak cgroup memory about 335 MiB. A separately cloned historical-ledger engineering probe produced four feasible proposals in 0.70 s wall / 0.47 s controller CPU, about 56 MiB process peak. The clone is not a scientific warm start. Original portfolio/random/reference arm hashes and evaluator epoch are unchanged.

E44c was preregistered and copied off-host before any scientific ledger existed. Registration SHA-256 `f260539ffd804d0c48a8653b9c048ab80b7055c8cfcc4f489fa28298a0a19c9a`; frozen source commit `5ad51c0101f05c7970fab236350f9ffd679afc16`; source archive SHA-256 `dd94ca3eacab7120cc465e587313c37b1bd1999b250c01ad1b41cd852290fe38`. New reference arm hash `0915661b4a66`.

The isolated runtime started November 14 at 04:12:15 Asia/Shanghai with two workers, 180% CPU quota and 1,500 MiB memory limit. First health check: eight completed trials, no terminal failures/restarts/LLM calls/unseals, matching identities, about 303 MiB peak memory. The other 22 workdirs had not started, not missing. Daily ledger backup is enabled; it is not continuous off-host checkpoint mirroring. Current display points to E44c; E44b remains in Past. Runtime source is frozen separately from later documentation commits. npm remains 0.6.6; this is not a new package release.

E44c's 64 evaluation CPU-hours should take roughly 36–48 wall hours conditional on capacity. Check health without quality-driven changes, then run the frozen analysis once after all budgets finish. Do not spend further rounds trying to rescue an unsupported controller: the next broader priority is useful retained models and a separately admitted real-data neural gym, with any controller/LLM/Jev intervention earning its own comparison.
