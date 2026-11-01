# Search map: observed models and search paths

Next-version design and an exploratory interactive replay prototype, not a deployed search controller. Keep the map as another view of existing evidence; it cannot influence ranking, proposals, evaluation or retention by merely being opened.

## Place and initial projections

Put Compare / Map views inside **Arms (4)**, which already compares concurrent search strategies. Overview can link to it; Leaderboard opens the selected model on the map; Past retains immutable end-of-study map data and replay manifests. Do not add another numbered top-level page.

The preferred exploratory view is a **2.5D configuration landscape**. Its plane is a fixed projection of the active typed model configuration; coloured shapes identify declared strategies. The height can show either evaluated-trial density or the best observed development score in a projection cell. A model point remains linked to its exact original score/spec. Stars mark observed best models, rings mark latest completed evaluations, and selected recorded lineage provides the paths.

Cell aggregation is a visual compression, not a continuous performance function. Unevaluated cells remain blank/lightly indicated as unknown. There is no interpolation into unobserved space or claim that every configuration in a good-looking region is good. Changing the height mode changes the question: density shows where effort went; best-score height shows where a development-selected promising observation occurred. A repeatedly visited cell has a different selection opportunity; its minimum is not an equal-budget causal comparison.

Keep an **efficiency projection** available as a research drill-down: X = nominal parameter count on a log axis, Y = development relative MSE (lower is better). This provides interpretable numeric context without pretending the two axes define full architectural similarity. Models sharing coordinates remain distinct; expose overlap counts and active-parameter diffs.

The initial landscape PCA uses 61 encoded variables over 875 actual seed-8 S2 observations. Numeric fields use fixed contract ranges, categoricals use scaled one-hot values, and inactive fields are zero with explicit activity indicators. Constant/irrelevant dimensions do not invent a trajectory. Freeze the feature list, centering, components, sign convention, binning and source-set identity. Its two components explain 53.8% of variance of this particular encoding: this is not 53.8% of scientific meaning or proof of semantic distance. The replay projection was fit retrospectively to the full frozen record, not online at each budget.

For prospective live runs, fit/freeze the projector on a declared earlier reference bank before the run; do not use future trial outcomes to construct an allegedly online map. If a later projection is useful, publish a new version and explicit transition. New observations may extend the visible footprint without changing old coordinates.

The landscape footprint stays fixed; budget replay reveals cells and updates observed heights without relocating old configurations. In the prototype, 22 × 16 projection cells and fixed full-history height scales support stable replay. Visited-cell counts describe this projection only; never label them a percentage of all possible models. A full free-camera 3D scene remains optional later: rotation, occlusion and terminal legibility need their own justification. Start with fixed isometric 2.5D and explicit selected-model details.

## Motion, progress and directions

Coordinates and domains stay fixed during replay. New observed points appear when evaluations complete; cell density/observed-best height can change with newly completed evidence. A moving marker means a newly completed evaluation, not an inferred physical location of an Agent. Keep pending/working proposals in a separate queue/lane until their score exists; never animate them downhill toward a guessed score.

The main replay clock is each strategy's own cumulative charged CPU budget, consistent with the experiment's endpoint convention. Label it CPU-seconds, not wall-clock time. Wall-time progress can be an explicitly separate operational view. Compare only a compatible epoch/contract/stage/cap and the declared matching seed or run set. Multiple seeds require facets or an explicitly labelled aggregate, not hidden pooling.

Draw a parent edge only when the ledger records the relationship, both model records exist in the compatible scope, and the parent is available at that replay time. Do not join consecutive evaluated points as if the second descended from the first. Missing parents are shown as missing references. A path may branch, revisit a configuration, get worse, or terminate; do not smooth those events away.

“Changed direction” is a recorded structural edit or strategy allocation change, with an active-parameter diff and provenance. It is not an arrow inferred from a two-dimensional line slope. An unproductive branch remains visible if evaluated. Empty map regions mean no observations, not low quality or unexplored proof.

## Data and performance

Build a read-only map cache from trial completion events, immutable specs, actual origin keys and evaluation identities. Each point needs run/arm/seed/epoch, trial/spec id, stage, nominal count, exact original score, charged-budget position, structural signature, parent ids and retention references. Keep original timestamps and failure outcomes in companion lanes. Store projector/filter/aggregation versions and the input ledger identities alongside snapshots.

The terminal subscribes to bounded incremental batches, never rescans every historical ledger every frame. Use a cell/point index and conservative point aggregation for the visible viewport; preserve exact records for Enter. Bound rendering frequency, honour reduced motion and ASCII fallback, and never slow the evaluator to animate a dot. Hover/tap/Enter details must state hidden dimensions and distinguish a new refit from the original scored model.

For a static Past snapshot preserve the underlying point/edge data and projection spec, not only a PNG. Replays and export must work from a verified local bundle without the original live server.

## Prototype evidence

The initial interactive concept replays **actual E43a seed-8** portfolio and portfolio_screen records: 875 finished feasible S2 observations at ≤3,000 nominal parameters and ≤50,400 charged CPU-seconds. The budget accumulates done-trial CPU in original finish order, matching the registered script convention. Other caps and this visual exploration do not replace the registered paired analysis.

Both the efficiency and 2.5D landscape prototypes use fixed full-replay coordinates/scales; budget scrubbing reveals only observations available by that budget. The PCA transform, feature rules and encoded-variance receipt are preserved locally. Selecting a point draws an edge only to a recorded available parent. The prototype does not show LLM Agents: these E43 runs made zero LLM calls. It demonstrates evidence-grounded map behaviour, not the proposed terminal integration, controller state, or a complete architectural search-space projection.

## Acceptance gates

1. Scope/clock/filter parity with original ledgers and budget endpoint; no mixed epochs or future points.
2. Parent integrity, missing edges, repeated specs and multi-parent edits handled honestly.
3. Fixed projection replay, bounded rendering, keyboard/touch selection and point-collision detail.
4. Long-run incremental refresh, stale cache and unavailable source recovery without changing running searches.
5. Saved/Leaderboard/Past linkage by stable identities; snapshot exports retain source hashes and exact records.

The existing 6/7/8 operational charts and Saved viewer can ship independently. Do not block their review on a full search-space map implementation.
