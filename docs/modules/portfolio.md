# portfolio

Operator portfolio and meta-controller: the member operators (sweep, evo, de, cem, aco, sa, qd, tpe, llm, random as the last resort) under `PortfolioProposer`. `evo_percap` is a standalone baseline arm defined in this module, never a portfolio member.

**Interface**
- Decomposition: discrete skeleton (swept, GA/ACO/QD/LLM) x continuous refinement per niche (DE, Gaussian EDA, TPE).
- `PortfolioProposer.allocation`: Thompson sampling on discovery rate + descendant credit; shortfalls re-allocated by posterior; bounded diversification burst on stagnation (`stagnation_n` = 300 evaluations).
- `build_portfolio(c, seed, client, memory_fn, with_tpe)`; `QDProposer`; `LLMStrict` is the LLM operator inside the portfolio (never falls back). `PerCapEvoProposer` (`evo_percap`) is not in that set: `make_proposer` runs it as its own arm, the fair baseline for capped-frontier search (independent elitist evolution per parameter-cap bin), outside the meta-controller's allocation.

**Invariants**
- Operator exceptions are caught, counted and the operator is skipped for that call; random is the last resort only.
- Retired discrete values are filtered with a 5% probe rate; a short-term tabu blocks the same failed single-lever step from the same parent.
- Each operator gets the campaign's `History` (`hist=`), so under `ants run` the statistics are epoch-filtered; `LLMStrict` builds its own prompt history without a filter (see [history](history.md)).

**Notes / failure behaviour:** Compared against single operators on `bench` landscapes; see docs/evaluation/E5-mechanism.md. No claim that it beats random at equal budget on real data.
