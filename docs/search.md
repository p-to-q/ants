# How ants searches

The picture that fits: a field of climbers on a mountain range in fog, each with a different method, sharing what they find, none of them told where the summit is. This page says what in the code is that picture, what is not, and what is missing. Evidence for the gaps comes from the first eight hours of three searches ([roadmap](roadmap.md)).

## What exists

| idea | in the code | where |
|---|---|---|
| many climbers with different methods, competing | a portfolio of nine search operators (random, a skeleton sweep, evolutionary mutation and crossover, differential evolution, CEM, ant-colony, simulated annealing, quality-diversity, TPE), plus the model operators `llm` and `agent` when a client is configured | `portfolio.build_portfolio` |
| the arena: whoever finds ground worth a deeper look gets more of the next batch | a Thompson-sampled bandit over operators; credit is a proposal being promoted to the next stage, with lineage credit passed back to the operator that made the ancestor; every operator keeps at least 4% of a batch, the model operators a guaranteed 20% | `PortfolioProposer.allocation`, `history.delayed_credit` |
| not trusting one path | quality-diversity keeps the best model per niche of the search space, not one best overall; a stagnation trigger spends one bounded burst of 30 proposals on the diversifying operators when nothing improved for 300 evaluations; spent ground is tabu for a while | `portfolio.QDProposer`, `PortfolioProposer`, `history.tabu` |
| crossing a ridge you cannot walk | a small quota (5%) of specs that break the parameter cap is kept as stepping stones, because a model slightly over the cap can seed a lineage that comes back under it | `Campaign._refill`, contract `stone_quota` |
| climbing in stages, cheaply first | a cascade: a cheap first look (s0), then deeper tests (s1, s2); only the better quarter of each size cap goes on | `Campaign._should_promote`, contract `promote_quantile`, `promote_by_cap` |
| several summits, not one | the result is a frontier: the best model per parameter cap (100, 300, 1000, 3000), each a separate hill | `ui.leaderboard` |
| an honest altimeter | the evaluator is fixed and out of the searchers' reach; the best-so-far only counts at the deepest stage reached; confirm and sealed data are looked at only by the gate | roles, epochs, `gate.seal` |

## What it does not assume

ants does not assume a summit exists or that it will be found. There is no convergence test and no stopping rule that says "done": a search runs until it is stopped, and the only statements it makes are about the best value so far at a stage, per cap. That is deliberate, but it leaves a question open: **how far from the top are we?** Today the only yardstick is a ceiling proxy: an unconstrained closed-form ridge (518k parameters) reaches rel_mse 0.79 on the development data ([open questions](open.md) 1), and the three arms found 0.78 to 0.80 under 3000 parameters. For the small caps there is no yardstick at all.

## Where the picture and the code part ways

Measured on the first eight hours (one seed per arm):

| finding | meaning for the picture |
|---|---|
| all three arms converged on the same recipe (period core, last-value normalisation, lookback 567 to 684, no pre-training) | the climbers crowded onto one slope; the others were not given a fair look |
| 35 to 45% of proposals are coarse duplicates of earlier ones | steps wasted on ground already stood on |
| the 100 and 300 parameter caps were almost never promoted (a global cut sent the larger models deeper) | two of the four hills were not climbed; fixed in epoch 2 by promoting per cap (the first hour of epoch 2 reached stage s2 in the ≤100 cap in all three arms) |
| the cheap stage orders near-equal candidates at random (rank correlation with the deeper stages about zero where decisions are made) | the fog is thickest exactly where it matters; roadmap steps 5 and 6 |
| the one-shot model operator never contributed (starved by the bandit, and every proposal rejected for missing keys) | a climber who never left the camp; fixed in 0.5.1, evidence starts there |
| two runs of the same method end at different recipes (ens 1 against ens 3) | the landscape has more than one good basin; one run cannot tell how many |

## What would make it more of what you describe

| idea | what it adds | cost | status |
|---|---|---|---|
| several seeds per arm, and a statement of how often runs agree on the peak | the number of basins, and the spread that any comparison must beat | 8 seeds, [roadmap](roadmap.md) step 2 | planned |
| snap jitter-only levers to a grid in the spec key | fewer wasted steps; the duplicate share falls | a day | not in the roadmap yet |
| a share of every batch for restarts from random or far niches, not only from the best | breaks the crowding without waiting for stagnation | a day | planned with the parent-rule arms (step 11) |
| ceiling references per cap (tuned ridge and hand-built baselines at each cap) | an altimeter: how close is 0.80 at 3000, and is anything possible at 100 | 0.5 core-days, step 7 | planned |
| a better cheap stage (more than one task) | less luck in who gets promoted | step 6 | planned |
| a tournament between whole arms (stop the one that is clearly behind, give its core to the others) | compute follows evidence, as the bandit does inside an arm | needs the equal-budget comparison first | deferred; never for efficacy inside a pre-registered comparison |
| a recorded estimate of headroom: the gap between the best found and the best reference, per cap, in the interface | the answer to 'how far' on the home screen | small | planned after the references exist |

The short answer to "is it like that": the mechanisms for many methods, shared credit, diversity and stepping stones exist and run; the claim that they find *more* than one method would is not yet shown, and the measurements say the search is still too crowded and too blind at the cheap stage. The roadmap is built to test that.
