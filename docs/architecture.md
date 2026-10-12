# Architecture

Three kinds of thing, kept apart on purpose: what **proposes**, what **judges**, what **executes**. Only the middle one is
trusted, and nothing on either side of it can reach into it.

```
  PROPOSE (untrusted, cheap)          JUDGE (deterministic, frozen)            EXECUTE (sandboxed)
  ─────────────────────────           ─────────────────────────────            ───────────────────
  operators: random sweep evo         Contract   typed space, caps, stages     Runner   clean env, process
  de cem aco sa qd tpe                Space.validate  closed grammar                    group, time + memory
  language models (llm)               static cost / memory pre-filter                  limits
  portfolio (Thompson sampling,       Cascade    S0 → S1 → S2 promotion        Gym      evaluate(spec, stage)
  lineage credit, per-parent tabu)    Registry   ladder + verdicts                      → metrics.json
  hints from a human                  Gate       freeze → sealed look
        │                             Ledger     hash-chained, epoch-fenced
        └──── Spec (data) ───────────────▶ │ ◀──────────── Result (data) ─────────┘
```

## One trial

1. **Propose.** The portfolio picks an operator by the rate at which it has recently improved the archive and asks it for a spec.
2. **Validate and price.** `Space.validate` rejects anything outside the grammar. A static cost (parameters) and a memory estimate
   reject certain violations before any CPU is spent; a small quota of over-cap stepping stones is kept on purpose.
3. **Enqueue** in the ledger under the current epoch.
4. **Claim and execute** as a subprocess with a fencing token, a stage timeout and a clean environment; the whole process group is killed on timeout.
5. **Promote** by stage quantile (S0 cheap, S1 rolling-origin on several families, S2 full dev set).
6. **Record.** One `trial_finished` event carries the full result; cheap deterministic stages keep ledger rows only.
7. **Confirm and claim** happen elsewhere and rarely: paired tests on confirmation data (E2), then freeze and one sealed look (E7).

## Layers of evaluation

Eight, each answering one question; see [evaluation/README.md](evaluation/README.md). The leaderboard and the board are projections.

## Modules

One page per module in [modules/](modules/README.md). Dependencies point inward: `cli`/`ui` → `campaign` → `ledger`, `runner`,
`history`, `portfolio` → `contract`. The search loop is standard library only; `metrics`, `registry`, `data` and the gyms need numpy and live at the edge.

## Extending

A new domain is a **gym** plus a **contract**: an `evaluate(spec, stage)` that writes `metrics.json`, and a TOML contract that
declares the typed space, the cap, the stages and the roles of its data. Nothing in the core knows about forecasting.
