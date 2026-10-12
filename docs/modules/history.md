# history

Incremental, bounded in-memory view of one arm's finished trials. One epoch only when the caller passes `epoch=`; with `epoch=None` (the default) it ingests every epoch.

**Interface**
- `History(ledger, c, arm, epoch=None, max_records=60_000)`; `update()` tails the ledger by `(finished_at, id)` in batches of 20 000 and returns when complete. Only `done` trials whose metrics contain the primary metric are ingested.
- `ranked`, `cells` (QD archive), `niches`, `traj`, `ops` (discovery rate), `credit`, `tau` (pheromone), `tabu` (per-parent), `stage_sorted` (per-stage quantiles), `stage_cut`, `percentile`, `weights`, `retired`, `is_tabu`.
- `trajectory/potential` (E4); `delayed_credit` (E5); `feasible` (the cap is a constraint); `cap_bin`.
- Memory bound: `max_records` -> old per-trial records are evicted (top records, niche elites and all aggregates are kept; the ledger keeps everything).

**Invariants**
- Over-cap records stay in the lineage and the archive but never lead, rank, or reward an operator.
- Operator success is decided BEFORE the archive is updated.
- Because `Ledger._stamp` never lets `finished_at` go backwards, the cursor cannot skip a result after a clock step.

**Notes / failure behaviour**
- Who filters by epoch: `Campaign` (its own `History`, `seen_keys`, `frontier`, `freeze_best`). Who does not: `ants best/top/show` ([cli](cli.md), [ui](ui.md)), the board, `bench`, `proposers._hist` when no history is passed in, and the LLM proposer's prompt history (`LLMProposer._ranked_for_prompt`, also inside `portfolio_llm`). After an evaluator or data change those can show or prompt with results measured by older machinery.
- Cost is O(new rows) per update. The module docstring's "~200k trials/day" is an intent, not a measurement.
- First-stage records only feed operator, lever and niche statistics; deeper stages feed `best`/`ranked` and `stage_sorted`.
