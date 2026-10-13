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

## A redesign of the search layer (proposal, nothing built)

The question behind it: nine operators were chosen because each is a known family, not because each earns its place here. The first eight hours say they do not all earn it (shares are proposals that reached the deepest stage s2; one seed per arm, so read the order, not the decimals):

| operator | share of its proposals reaching s2 | note |
|---|---|---|
| evolutionary crossover | 72 to 84% | every top-10 spec in all three arms came from evolutionary mutation or crossover |
| evolutionary mutation | 48 to 57% | as above |
| differential evolution | 7 to 13% | |
| TPE | 7 to 9% | |
| random | 3 to 7% | the control; also the only one that cannot get stuck |
| quality-diversity | 2 to 5% | its job is coverage, which this number does not measure |
| ant-colony | 0 to 1% | |
| model operators (after the 0.5.1 fix) | too early | |

**What the field would call this problem.** A multi-fidelity, multi-objective hyper-parameter and architecture search over a conditional mixed space (a lever matters only for some cores), with noisy evaluations: error against parameter count. The standard tools: successive halving and its asynchronous form (ASHA, Hyperband) for the stages; BOHB or SMAC (a surrogate model that handles conditional spaces) for sample efficiency; NSGA-II or ParEGO for the error-versus-size frontier; regularised (aging) evolution, the workhorse of neural architecture search under a memory budget; MAP-Elites and CMA-ME for diversity; evolution through language models (FunSearch, AlphaEvolve) when the variation operator is a model. Ours maps onto these but is built differently: a bandit over operators ("adaptive operator selection" in the literature), a quantile promotion rule, the caps as four separate bins, stepping stones. None of it has been compared with the standard versions at equal budget, which is the gap to close first.

**Proposed shape** (each piece replaces something, not adds to it):

| layer | now | proposed | why |
|---|---|---|---|
| stages | a quantile cut per stage and cap | rungs of ASHA with a stated reduction factor; the rung fidelity chosen by measured rank correlation, not by habit | the cheap stage has near-zero rank information among near-equal candidates; successive halving is the standard answer and comes with analysis |
| main engine | nine operators | regularised (aging) evolution with tournament selection as the exploiter; one surrogate-assisted proposer (TPE or a random-forest surrogate, SMAC-style) as the explorer; random as the control; the model operators as variation and analysis operators | the data say the exploiters find what is found; the rest cost evaluations |
| objective | four independent caps | one archive ordered by Pareto dominance on (error, parameters), with niche cells by size and core type (MAP-Elites) | a model found at 1000 parameters is often a seed for 300; separate hills throw that information away |
| noise | best of N single evaluations | re-evaluation (racing) of the top few before they count as best; report best-of-N and confirm score side by side | the best of many noisy evaluations is optimistic |
| waste | 35 to 45% coarse duplicates | grid keys for levers that only jitter | free |

**Evolving the searcher itself.** Yes, and it can be done in the order biology does it, with the same care about what selection pressure means:

1. *Selection among operators* exists (the bandit). It is selection without variation, and its fitness signal is proposals promoted, which is noisy and favours cheap, safe moves.
2. *Variation inside an operator*: let an operator's own settings (mutation rate, tournament size, step scale) be a small genome that is mutated and selected by discovery rate per CPU-second, not per proposal. This is the self-adaptation of evolution strategies and costs little.
3. *Evolution across runs*: each finished run (a seed) is a generation; the final operator shares and settings become the prior of the next run. Selection is slow on purpose, one step per run.
4. *Variation by a model*: a language model as the mutation operator over specs (the agent arm is the first version), and later over the operators' code, scored by replaying the ledger. That is the Meta-Harness direction and is recorded as roadmap step 14 in the research note.
5. *Guard*: selection at the meta level uses the same dev-data signal, so it can overfit the dev data just as the models can. Meta-level choices are checked on confirm data once per run, never per step.

**The honest order of work**: add the standard baselines as reference arms (Optuna TPE with a Hyperband pruner, SMAC, NSGA-II, aging evolution, random) in the equal-budget experiment; run a leave-one-operator-out ablation of the current portfolio; then decide what to drop and what to build. The proposal above is a hypothesis about the outcome, not a plan to act before it.

## Toward the landscape view (proposal; a research phase comes first)

A criticism worth taking seriously: much of the current framework uses the plainest tools. Operator weights come from a promotion rate; stages cut at a quantile; the four caps are weighed apart by hand; datasets are averaged with family weights chosen by judgement. The fields this problem belongs to (global optimisation of expensive noisy functions, fitness-landscape analysis, evolutionary computation, multi-objective and multi-fidelity optimisation) have mature, quantitative ways to do each of those things. The aim is to move to them, and to measure the landscape instead of guessing at it.

**The four aims, and the established machinery for each**

| aim | established machinery | what it would mean here |
|---|---|---|
| 1. better models | a probabilistic model of the landscape (a surrogate with uncertainty) and a rule that spends compute where the expected gain per cost is largest: expected improvement or knowledge gradient per CPU second; expected hypervolume improvement for the error-versus-size frontier; multi-fidelity so that cheap stages are used for what they can tell | evolution, annealing and diversity become *policies on top of the model*, not rivals to it |
| 2. better weighting direction | weights as inferred quantities: operator weights as a posterior over discovery per unit cost (Thompson sampling with a proper reward, hypervolume gain per CPU second instead of 'was promoted'); lever relevance learned from the data (automatic relevance determination, functional ANOVA) and used as the metric of the space (for niches, novelty, step sizes); dataset weights from a hierarchical model with partial pooling instead of hand-set family weights; objectives kept as a Pareto order, never summed | the weights stop being constants someone chose and become estimates with error bars |
| 3. a more careful look at the landscape | fitness-landscape analysis: ruggedness (autocorrelation along walks), neutrality (how many levers do nothing, as `prune` jitter does), local-optima networks, the number and size of basins from repeated runs, lever importance and interactions, two-dimensional slices through the best models, the surrogate's uncertainty as a map of where nothing is known | a landscape report per epoch, and a view in the interface |
| 4. faster to the target | multi-fidelity racing (successive halving and its descendants), warm starts from earlier epochs and from neighbouring datasets (meta-learned priors), early stopping rules with a stated risk, cost-aware proposals, replica exchange between arms (parallel tempering) and island migration | fewer core-hours per unit of progress, measured |

**What biology and annealing give, stated precisely.** Selection pressure and temperature are *schedules*: how greedy the search is, as a function of what has been learned. Annealing accepts a worse move with a probability set by a temperature; evolution sets pressure by tournament size and population; island models keep subpopulations apart and let a few individuals migrate; replica exchange runs the same search at several temperatures and swaps states between them. The landscape measurements of aim 3 say which schedule is right (a rugged landscape with many basins wants a higher temperature for longer; a smooth funnel wants pressure). So the order is: measure, then set the schedule from the measurement, rather than choosing an operator by habit.

**My view of the shape.** One idea unifies the aims: *a probabilistic model of the landscape, updated by every evaluation, with compute allocated by value of information*. Evolution, annealing and quality-diversity are then ways of generating candidates and of keeping the search honest about uncertainty; the bandit over operators becomes a hierarchical credit model; the cascade becomes a multi-fidelity rule whose fidelities are chosen by measured rank correlation. This is not a call to throw the portfolio away: the ledger already holds thousands of evaluations, so each of these ideas can be tried **offline, by replay**, on data we have, before any new compute is spent: fit the surrogate on the first part of a ledger and measure how well it ranks the rest; replay a policy against the recorded trials and count how many core-hours it would have needed to find what was found. That is the cheapest honest test, and it is roadmap steps 21 and 22.

**Where gradients and larger models come in, later.** The closed-form fit is right for now (cheap, exact, reproducible). A second track is natural once the landscape is understood: train *larger* models (more parameters, more steps, gradient-based) and compress them (quantise, prune, distil) down to each cap, then compare with what the closed-form search found at that cap. Whether compression-from-large beats search-at-size is an open empirical question in this domain; it is recorded as roadmap step 23.

## What the aim is, and what the search is for

The aim is better models and better directions, not a better search for its own sake. Two outputs follow from that and are not produced yet: a **direction report** (which levers matter and how much, by an importance analysis over the ledger; which regions of the space are exhausted), and a **training hand-off** (the winners' specs fitted again with more data and compute, and optionally fine-tuned by gradient with low-rank adapters, to see how much the cheap closed-form fit left on the table). The search is the instrument; these are the measurements.
