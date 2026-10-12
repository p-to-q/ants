# Principles

*ants: Agents Never Touch Scoring.*

ants is a machine for producing claims that survive being checked. These principles judge the running system, not how
ambitious an idea sounds. Each names what it forbids and where it is enforced; a principle without an enforcement point is
a wish and does not belong here.

1. **The proposer never holds the pen that scores.** Search operators, scripts and language models may propose a spec; none
   may edit the evaluator, the contract, the datasets' roles, or the ledger. Language-model output enters only through a
   closed, typed grammar and is otherwise data. *Enforced by* `Space.validate`, `maintainer` patch gate, `gate`.
2. **A budget is a wall, not a price.** A parameter cap is a constraint. Over-cap candidates may be kept as stepping stones,
   but they are never leaders, parents by rank, or reward signals. *Enforced by* `history.feasible`, the static-cost filter
   in `campaign`.
3. **Evidence is append-only and carries its own identity.** Every trial is stamped with an epoch (evaluator code, contract,
   data manifest). Results from different epochs are never mixed. Nothing that supports a claim is deleted; it is archived,
   verified, and only then released from disk. *Enforced by* `ledger`, `archive`, `janitor`, epochs.
4. **Look once.** Data has a role. Development data serves the search; confirmation data serves one test of the finalist;
   sealed families are looked at once, after the candidate is frozen, and every look is recorded. *Enforced by* the
   catalog roles, `ledger.freeze` / `record_unseal`, `registry` (no champion without a sealed result).
5. **Many comparisons demand a larger doubt.** A search that tries ten thousand candidates will find lucky ones. Promotion
   needs paired statistics over independent source families, a practical threshold, and a multiplicity control that
   spends a finite error budget. *Enforced by* `metrics`, `registry.decide_promotion`.
6. **A cheap proposer must beat a cheaper one.** Every clever operator, every language model, is compared with random
   search at twice the budget before it is believed. Arms are compared at equal evaluation budgets over several seeds.
   *Enforced by* `bench`, the arms in `campaign`, and the rule in [AGENTS.md](../AGENTS.md).
7. **Judge the path, not only the point.** A model that is best now is not necessarily on the road to better. The search
   keeps lineage, measures velocity and acceleration of each path, and keeps budget on paths that are currently losing.
   The next best model need not descend from the current best. *Enforced by* `lineage`, `history.trajectory`, `portfolio`.
8. **Judges earn authority.** A non-deterministic judge starts in shadow, is logged against resolved outcomes, and gains
   advisory then delegated authority only by measured calibration. No judge overrides integrity or the sealed look.
   *Enforced by* `judge` (authority ladder), E6.
9. **Failure is a normal outcome.** The system is built for the day nobody is watching: crashed processes, a full disk,
   a stepped clock, a hung child. It must stop cleanly, resume without double-counting, and say what happened.
   *Enforced by* `runner`, `campaign`, `janitor`, the chaos tests (`pytest -m slow`).
10. **Say what it is.** A leaderboard is a projection of the ledger, never its source. A check that was not run is written
    "not run", with the reason. Status words are `stable`, `experimental`, `internal`, `stub`, `archived`, and the table
    is [surfaces.md](surfaces.md). *Enforced by* review, and by `ants doctor`.
11. **Small is a research stance.** Ants are individually trivial; the colony is competent because it leaves a trail.
    ants prefers structure over scale: a hundred-parameter model that is understood is worth more than a larger one that
    is only fast. The metaphor is a reminder, not a mechanism. *Enforced by* nothing; it is the reason for the rest.

## Taste

How the work should read and feel, because a reader is also a user.

* Flat is better than nested; a table before a paragraph, a number before an adjective.
* Printable. Every report, leaderboard and `--json` output should survive being pasted into a terminal or a paper.
* Human-readable interfaces for machine-produced things: long, plain names; no private abbreviations.
* Simple, composable parts over a framework. If a module needs a diagram to be understood, split it.
* Overly abstract means narcissism: every general statement is followed by a number, a file or a test.
