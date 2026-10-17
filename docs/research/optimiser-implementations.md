# What the optimisers actually do: a reading of their source (R16, 2026-10-17)

Source read at these commits: Optuna `c8d040d` (5.1.0.dev), optunahub-registry `618066c`, SMAC3 `8414887` (v2.4.1), DEHB `2d39900`, HpBandSter `0ec0ce2`, nevergrad `55950f9`, pyribs `7bd723c`, OpenEvolve `a1e9d91`, ShinkaEvolve `8adc053`, BANANAS `e2c12ad`, naszilla `7e65491`; HEBO, BoTorch and Ax as single files. Papers were not re-read; "why it works" is a code comment or a pointer to [search-landscape](search-landscape.md). No benchmark was run. Not verified: the Optuna release ants pins (defaults differ from the development commit: `multivariate`, `constant_liar`), whether `TPEHistProposer` passes inactive parameters to `add_trial`, and the conditional-space behaviour of DEHB/BOHB at 25 or more dimensions.

## What each does that matters here

| system | decision | applies to ants |
|---|---|---|
| Optuna TPE | good set is `min(ceil(0.1 n), 25)` rows; `n_ei_candidates` 24 drawn from l(x) and ranked by log l - log g; without `group=True` the joint model uses only parameters present in every trial; the AutoSampler code carries a TODO to use `group=True` "once benchmarks are available" | the cap on the good set and the recency ramp apply; a conditional space needs per-trial active parameters |
| Optuna GP / CMA-ES / NSGA-II | intersection search space only; out-of-intersection parameters are sampled independently (NSGA-II re-randomises them in every child) | not for a conditional categorical space |
| Optuna / SMAC pruners | asynchronous successive halving: promote the top 1/eta of the values seen at a rung | same shape as ants' quantile rule; Hyperband brackets do not fit (fidelity here is a stage, s1 and s2 agree at 0.995) |
| OptunaHub AutoSampler | static rule on parameter types and trial count; categorical or conditional spaces always go to TPE | ants would always land in TPE, the operator with a 0.000 top-decile share in the ledgers: not a model |
| SMAC3 | random forest, inactive conditional values imputed (numeric -1, categorical a value outside the range); the acquisition maximiser ranks 10 000 random configurations plus local searches from the best 10 and previous configurations, and returns a **ranked list**; 8 are taken per model fit with 20% interleaved random; trained at the highest budget only | the closest published shape to the plan: many cheap candidates, rank by surrogate, evaluate few |
| BOHB / DEHB | one model at the largest budget with enough rows; conditional gaps filled by copying the value from a random row where the parameter is active | confirms the single-fidelity plan; a better KDE fill than defaults |
| BANANAS | 100 mutants of the current best, deduplicated by hash; an ensemble; **independent Thompson sampling** (a draw from N(mean, spread) per candidate); the 10 lowest are evaluated per round | same pattern at small scale |
| aging evolution, local search (naszilla) | population 30, tournament 10, the **oldest** is removed; local search: 10 random starts, first-improvement moves, restart at a local minimum | the White-Nolen-Savani implementation of the strong baseline |
| BoTorch / HEBO | cost-aware utility = mean gain / mean cost; batch picks include the best-mean and the highest-uncertainty point | ants' `ens` lever triples cost: expected improvement per CPU-second |
| Nevergrad | noiseless discrete space defaults to a (1+1) evolutionary algorithm with a heavy-tailed number of mutated parameters (p proportional to u^-1.5, up to max(2, ratio x dimension)) | take the mutation-size law, not the selector (nothing is learned online) |
| ShinkaEvolve | parent weight = sigmoid(10 (score - median) / MAD) x 1 / (1 + children); at most 3 novelty attempts; migration rate defaults to 0 | child-count factor stops one elite absorbing all offspring |
| OpenEvolve | parents: 0.2 random from the island, 0.7 from the elite archive, 0.1 fitness-weighted; ring migration every 50 generations at 0.1 | the mix, yes; migration rate has no evidence either way |
| pyribs | soft-threshold archive; a UCB1 bandit over emitters | only the soft threshold and the bandit over emitters |

## Six changes for the search engine, in order (each with a sketch in the survey; none is built)

1. **Candidate pool, surrogate ranking, few evaluated.** Mutation is free: generate thousands of candidates from several roots, rank them with the extra-trees model of `screen.py`, evaluate about eight, refit after them, keep a fifth unscreened.
2. **Thompson ranking and a cost-aware score.** A draw from N(mean, spread) per candidate instead of mean minus 0.5 spread; expected gain divided by predicted CPU (`ens` 1, 2, 3 cost 12.9, 21.5, 33.5 s at s1). The selection probability is then only estimable by Monte Carlo, so log the draw seeds (roadmap step 35).
3. **Several roots, aging evolution, heavy-tailed mutation size.** R independent lineages (4 to 8), each a fixed-size population where the oldest leaves; restart a stagnant lineage from a new root. A lineage that wins more than once is evidence, not luck, which also gives the "one root decides" finding a replication check.
4. **Conditional-aware encoding.** Keep -1 for the trees; give the TPE operator only each trial's active parameters with `group=True`.
5. **Parent weights and near-duplicate rejection.** The sigmoid-over-MAD weight divided by (1 + children); up to 3 attempts to get a child farther than a distance from the nearest evaluated one (the ledger's median nearest distance for `evo` is 0.004, so the threshold has to come from the ledger).
6. **Batch diversification.** Without it the best eight of one pool are eight siblings: take the best-predicted, the most uncertain, and the rest greedily by distance.

## Not to copy

Hyperband brackets; AutoSampler's selector; GP samplers (cubic cost at thousands of points, no conditionals); NSGA-II and CMA-ES samplers (conditionals re-randomised); DEHB's default-filled unit cube (the conditional blindness `DEProposer` already has; DE is at 0.001 top-decile in the ledgers); Nevergrad's selector; embedding or LLM novelty judges; BANANAS' neural ensemble; any migration rate taken from these systems' defaults; ParEGO weights across four independent caps.

## What it adds to the plan

Changes 1, 2 and 6 are the shape of step 43 grown from a promotion rule into a proposal engine (roadmap step 18); change 3 is the K-root restart question of step 44 asked inside one run; change 4 is a one-line check of `TPEHistProposer` and a cheap fix for the floor arm of step 16. None of it is started until E43 says whether the screen helps at all.
