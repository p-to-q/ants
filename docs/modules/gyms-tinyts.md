# gyms-tinyts

TinyTS: closed-form-trainable ultra-light forecasters across the whole training life cycle. Command: `python -m antslab.gyms.tinyts --profile {clean|hard|catalog} --stage S --spec F --out D [--final]`. numpy only.

**Interface**
- `evaluate(spec, stage, data_dir, split, profile)`: synthetic ETT-like series (profiles `clean`, `hard`), pipeline validation only. `evaluate_catalog(spec, names, split, ...)` / `evaluate_stage_catalog`: REAL data from `data/catalog.toml` (profile `catalog`); stages `CAT_STAGES` s0 (etth1, H=96), s1 (etth1, weather, exchange; 3 folds), s2 (adds ettm1; deep metrics).
- Cores linear/period/fourier/basis; levers: clip, recency, augment, robust, pretrain+prior, LoRA-style adapter, distill, ensemble, prune, quantise. `count_params(spec)`, `est_mem_mb(spec)` are the contract's `static_cost` / `static_mem`.
- `--final` with `--profile catalog` evaluates the TEST windows of every real dataset whose role is dev, confirm or sealed (per-dataset `rel__<name>@H` metrics are emitted), pooled with the same family weights. It is reachable only by `gate.seal` and by hand; contract stages never reach it.

**Invariants**
- Parameters are counted at horizon 720 so caps are comparable across stages. Pruning is charged honestly: `charged_params` = min(dense size, nonzeros × (1 + ceil(log2 n) / value_bits)), and `Model.stored_params()` uses the same accounting (a test asserts `count_params == stored_params()` for non-adapter specs). A mildly pruned model therefore costs its dense size.
- `rel_mse` = MSE(model) / MSE(seasonal naive of the UNCLIPPED standardised series): the `clip` lever changes the model's inputs only, never the reference or the targets.
- Results are bit-for-bit deterministic for a given spec and stage (fixed RNG seeds, fixed chunk order, single-thread BLAS as set by the runner; nothing here touches thread settings).
- Rolling-origin validation: fold i trains only on targets before its block; test windows are never used by validation modes (tested). Headline `rel_mse` is family-weighted (`metrics.family_weights`, `gmre`).
- Pooled pre-training (`pretrain='pool'`) uses only TRAIN windows of dev datasets of other families (tested); synthetic pre-training uses seeds disjoint from the evaluation series.
- The memory estimate may not under-predict measured peaks (`tests/test_tinyts.py`; uses a 10% margin and skips where a child's peak RSS cannot be measured).
- Under `ants run` the process has `ANTS_ALLOWED_ROLES=dev`, so a stage that asked for confirm, sealed or stress data would fail with `RoleError`; the stage dataset names are fixed in code, not in the spec.

**Notes / failure behaviour**
- `evaluate_catalog` does not call `Catalog.require_roles` itself: called directly, with no policy active, it evaluates any dataset name and split, sealed ones included (see [data](data.md)).
- Dev and confirm share some families (ETT); only 4 dev datasets (3 families) steer the search, and the validation windows are a fixed deterministic sample.
- Evaluator correctness beyond the tests is not proven; the epoch only proves the code did not change.

**Memory and time design** (one plain s1 evaluation peaks at about 166 MB, the worst spec at about 371 MB, measured on macOS; it was 514 and 1013 MB)
- Window matrices are float32 (`windows` keeps the dtype of its input) and are built straight into their output or a reusable `Scratch` buffer shared by all horizons and members; the 4M-element cap per matrix (`MAX_WINDOW_ELEMS`) is therefore 16 MB.
- Fits are streamed: every core accumulates normal equations (`start/add/finish`, `Gram`) from float64 chunks of `CHUNK_ELEMS` elements, so no full-size float64 copy exists. The reduced-rank and adapter SVDs use the eigenvectors of `W^T (X^T X) W`.
- Augmentation noise is replayed from the saved RNG state instead of being materialised; scoring is streamed; pre-training priors are fitted once per (spec, horizon) and thinned at window construction.
- `est_mem_mb` is an additive model fitted to fresh-process peaks (plain about 365 MB, worst about 610 MB, cap 750). `rel_mse` moved by at most 6e-7 against the old code on the case table. **Not measured on Linux:** re-run `tests/test_tinyts.py -k "estimate or resident"` on the server before trusting the cap.
- The catalog path's floor is `data.sample_windows` (an int64 index matrix); a gather-by-channel version is described in the performance report and not merged.
