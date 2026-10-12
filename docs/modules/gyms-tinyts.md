# gyms-tinyts

TinyTS: closed-form-trainable ultra-light forecasters across the whole training life cycle. Command: `python -m antslab.gyms.tinyts --profile {clean|hard|catalog} --stage S --spec F --out D [--final]`. numpy only.

**Interface**
- `evaluate(spec, stage, data_dir, split, profile)`: synthetic ETT-like series (profiles `clean`, `hard`), pipeline validation only. `evaluate_catalog(spec, names, split, ...)` / `evaluate_stage_catalog`: REAL data from `data/catalog.toml` (profile `catalog`); stages `CAT_STAGES` s0 (etth1, H=96), s1 (etth1, weather, exchange; 3 folds), s2 (adds ettm1; deep metrics).
- Cores linear/period/fourier/basis; levers: clip, recency, augment, robust, pretrain+prior, LoRA-style adapter, distill, ensemble, prune, quantise. `count_params(spec)`, `est_mem_mb(spec)` are the contract's `static_cost` / `static_mem`.
- `--final` with `--profile catalog` evaluates the TEST windows of every real dataset whose role is dev, confirm or sealed (per-dataset `rel__<name>@H` metrics are emitted), pooled with the same family weights. It is reachable only by `gate.seal` and by hand; contract stages never reach it.

**Invariants**
- Parameters are counted at horizon 720 so caps are comparable across stages; the count is the number of non-zero weights (`prune` reduces it), not the stored size of a sparse format.
- Rolling-origin validation: fold i trains only on targets before its block; test windows are never used by validation modes (tested). Headline `rel_mse` is family-weighted (`metrics.family_weights`, `gmre`).
- Pooled pre-training (`pretrain='pool'`) uses only TRAIN windows of dev datasets of other families (tested); synthetic pre-training uses seeds disjoint from the evaluation series.
- The memory estimate may not under-predict measured peaks (`tests/test_tinyts.py`, load-sensitive).
- Under `ants run` the process has `ANTS_ALLOWED_ROLES=dev`, so a stage that asked for confirm, sealed or stress data would fail with `RoleError`; the stage dataset names are fixed in code, not in the spec.

**Notes / failure behaviour**
- `evaluate_catalog` does not call `Catalog.require_roles` itself: called directly, with no policy active, it evaluates any dataset name and split, sealed ones included (see [data](data.md)).
- A sparse/pruned model is charged by non-zeros only; index storage is not charged. The analytic count is tested against the stored weights of the four unpruned cores; `stored_params()` (non-zeros after pruning) is not compared with it.
- Dev and confirm share some families (ETT); only 4 dev datasets (3 families) steer the search, and the validation windows are a fixed deterministic sample.
- Evaluator correctness beyond the tests is not proven; the epoch only proves the code did not change.
