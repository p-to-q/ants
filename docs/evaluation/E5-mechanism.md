# E5 Mechanism - are our search operators and levers working?

**Emits:** re-allocation of effort among *operators* (random, evo, qd, de, cem, aco, sa, tpe, llm, sweep) and among *levers* (spec parameters).

* **Discovery rate** of an operator = fraction of its proposals that improve their (skeleton x cap-bin) archive cell or open a new one. (Not "top 15% of history": history is mostly junk, any exploiter clears that bar.) Over-cap children never count.
* **Descendant credit** (back-propagation through lineage): every ancestor of a current top-k record credits the operator that produced it, discounted by distance. Stepping-stone operators whose payoff is late are valued instead of starved.
* **Lever credit:** per parameter, the mean positive gain of children that changed it versus their parent (`history.weights`), with an exploration bonus.
* **Allocation:** Thompson sampling over the above; operators that under-deliver have the shortfall re-allocated by posterior, not dumped on random; stagnation triggers one bounded diversification burst.
* **Short-term tabu:** the same failed single-lever step from the same parent is blocked for 40 evaluations. (A statistical "bad direction" ban was tried and removed: it filtered almost every lineage-bearing proposal.)

## Controls that keep this layer honest
* **Per-cap evolution** (`evo_percap`): an independent elitist search per parameter bin. A single global incumbent search is a strawman on a capped frontier: it drifts to the largest cap.
* **Random at 2x budget** (the classic strong baseline from the multi-fidelity literature) and **TPE** on the same spec space (a tuned closed-form ridge is already known to be strong in this arena).
* The static cost pre-filter is applied to every arm, so budgets are comparable.
* Findings so far are in `internal/`; they are preliminary (synthetic data, few seeds) and are not claims.

## Status
Implemented and running inside `ants run` for the `portfolio*` arms: discovery rate, descendant credit, lever credit (`History.weights`), Thompson allocation, tabu, retirement and the diversification burst; the numbers are visible in `ants top` / `ants best --json` / the board. The batch `credit.py` is a report helper, not part of the loop. **Not run:** an equal-budget comparison against random search at 2x budget and against TPE over several seeds on the real-data gym, so there is no claim yet that the portfolio beats them ([open question 1](../open.md)). The statistics are epoch-filtered only inside the campaign; the LLM operator's prompt history and the plain `tpe` arm read all epochs.
