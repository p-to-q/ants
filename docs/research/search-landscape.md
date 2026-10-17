# Search over a landscape: what the literature says, and what it means for ants

Research phase R of the [roadmap](../roadmap.md), first reading 2026-10-17. Three independent readers went through the original papers (PDFs read page by page; summaries from web tools were not used) in three groups: landscape analysis and credit assignment (R1, R5), multi-fidelity, multi-objective and quality-diversity methods and tiny-budget search (R2, R4, R6), and evolution, annealing and replica-exchange schedules (R3). Everything below that is not marked otherwise was read in the source; items marked *not verified* were not. **Nothing here has been tested on our system.** The recommendations are hypotheses with a stated offline test.

Our problem, in the field's words: a multi-fidelity, two-objective search over a conditional mixed space of about 40 levers, evaluations deterministic with data-sampling noise, cost 0.6 s to 77 s, two cores, thousands of evaluations a day, fidelity = number of datasets and folds (not epochs).

## What the evidence says, by question

| question | evidence read | reading |
|---|---|---|
| How rugged are such landscapes? | Pushak and Hoos 2018: 99.48% of 193 one-parameter slices around tuned configurations were unimodal and convex; Huang and Li 2024: 1 500+ hyper-parameter grids, nearly unimodal, highly neutral, flatter near the optimum | benign on average, which argues for exploitation with a good local model, and against heavy exploration machinery; both studies cover few levers and no conditional ones |
| Does a cheap fidelity predict the expensive one? | Huang and Li: low against full fidelity Spearman median above 0.85, but overlap of the top 10% only above 60% (0.07 for one train-versus-test case); NAS-Bench-101 (Ying et al.): Hyperband and BOHB gave little speed-up because ranks across budgets correlate little; BOHB's authors: at 64 dimensions TPE and SMAC beat BOHB because low-budget noise grows; Egelé et al.: keeping the top-K after one epoch matched Hyperband and successive halving with 40 times fewer epochs when the short horizon is not biased | our s0 behaves like the cases where rank agreement collapses at the top; the standard cure is to measure it per cap and weight the stage by it |
| Which exploiter is the strongest default? | Real et al. (aging evolution): beat reinforcement learning and random search; NAS-Bench-101: ranking random search < non-regularised evolution < regularised evolution, TPE fell to random-search level; DEHB (Awad et al.): mean final-regret rank RS 7.46, Hyperband 6.54, BOHB 4.42, TPE 4.35, SMAC 4.73, RE 3.16, DE 2.96, DEHB 2.39; µNAS: aging evolution gave a better Pareto front than Bayesian optimisation, attributed to a Gaussian-process surrogate being too smooth with few points | consistent with our own result (every top spec from evolutionary mutation and crossover); aging evolution, DE and DEHB are the natural references |
| Do portfolios of operators beat the best operator? | Tong et al.: a UCB portfolio ranked 2.00 against 2.60 for random operator choice, and beat the best single algorithm on 3 of 15 problem rows (5×D evaluations); Dynamic-MAB (DaCosta et al.): all methods fail on an outlier scenario, static MAB wins on the boolean one; Li and Talwalkar: random search with early stopping matched ENAS | gains are modest and small-budget; the burden of proof is on the portfolio |
| Does a surrogate help in many mixed conditional dimensions? | SMAC3: random-forest surrogate handles conditional hierarchies; µNAS and BOHB's failure at 64 dimensions above | no evidence found at a few thousand points in about 40 conditional dimensions; test offline |
| Do diversity methods raise the final best? | CMA-ME (Fontaine et al.): wins on a deceptive task (Hearthstone win rate 65.9% against 44.8% for CMA-ES), loses on Rastrigin with n=100 (86.9 against 99.9) | diversity helps where the landscape deceives; whether ours does is measured by basin counts across seeds |
| What can honestly be claimed about operators? | Igel and Toussaint: no free lunch holds only for function classes closed under permutation, a vanishing fraction; structured landscapes escape it | 'operator X wins' is a statement about our landscape and needs seeds |

## What each part of ants maps to

| ants today | the field's version | verdict from the reading |
|---|---|---|
| quantile cut per stage and cap | per-rung promotion (ASHA, Li et al., MLSys 2020: 3.24% against 3.42% for synchronous halving and 3.36% for BOHB on one NAS task); ranking-loss weights per fidelity (MFES-HB, Li et al., AAAI 2021: 10.1 times faster than Hyperband on one benchmark) | adopt the weighting idea first: a fitted weight near zero for s0 would be a measurement of what we only suspect |
| four independent caps | one archive on (error, parameters) with a weight vector resampled each round (µNAS), cells by size and core type (MAP-Elites) | adopt; replay tests whether small caps benefit from larger ones |
| nine operators under a bandit rewarded by 'was promoted' | capped Beta posterior with a forgetting cap C (Sun and Li, dynamic Thompson sampling for MOEA/D; reward 1 if offspring improve the population); change-detection restart (DaCosta); reward by gain in the per-cap frontier at the deepest stage, zero for canonical duplicates (our adaptation; the hypervolume-contribution credit literature was *not verified*) | change the reward before changing the operators |
| TPE operator | random-forest surrogate (SMAC4HPO); DEHB for evolution inside fidelity brackets | compare as reference arms |
| noise: best of N single evaluations | re-evaluation of the top few at fresh data seeds (racing: *irace and F-Race not verified*) | measure regression to the mean first |
| QD operator | MAP-Elites cells on (core, size bin, lookback bin); descriptors from the per-dataset error vector (AURORA, Grillotti and Cully: learned descriptors approach hand-coded ones, hand-coded won on one task) | keep as a coverage tool; judge it by distinct basins found |
| stagnation burst | restarts with a larger population on stagnation (Hansen's CMA-ES tutorial); sawtooth schedules and re-seeding the worst islands (FunSearch) | candidate rule, untested for discrete genes |

## What does not suit

qNEHVI and Gaussian-process hypervolume methods (continuous space, smooth surrogate, expensive acquisition against a cheap metric); CMA-ME and CMA-ES as methods (continuous genomes; their restart and population-size rules are what carries over); Hyperband brackets and one-epoch rules as published (fidelity here is datasets, not epochs, and the top-rank correlation breaks their premise); landscape-feature algorithm selection (Kerschke and Trautmann: needs 50×d to 500 samples per instance, features are unreliable at small samples per Renau et al., and we have one problem family); local-optima networks as published (they enumerate the space; a sampled analogue is our own extension and unverified); population-based training at our population sizes (PB2: at a budget of four, PBT lost to random search on three of four tasks); replica exchange across our arms (swaps are meaningless when energies are noisy and stage-mismatched, and our runs are far from equilibrium); algorithm-selection by features.

## Adoption order, with the cheapest offline test of each

All tests below use ledgers we already hold (spec, stage, metrics, times, operator, lineage for tens of thousands of trials); none needs new compute.

| # | change | offline test |
|---|---|---|
| 1 | canonical spec keys: inactive levers collapsed, jitter-only levers on a grid | count unique canonical keys; Hamming-1 neighbours with equal rel_mse (tolerance as in Huang and Li) size the neutral plateau |
| 2 | stage agreement per cap: Spearman and top-quartile overlap of s0, s1 and s2; MFES-style ranking-loss weights | compute from the ledger; if the s0 weight is near zero, s0 is a filter for gross failures only |
| 3 | noise: re-evaluate the top 20 at fresh data seeds | regression to the mean of the current 'best'; chance the top-k order flips |
| 4 | lever importance and interactions by functional ANOVA per stage and cap, inactive levers imputed, a y-max cap to the good region (Hutter et al.; van Rijn and Hutter for the spread across datasets) | stable across the three runs and in held-out prediction; names the inert levers |
| 5 | lineage diagnostics: parent-child autocorrelation per operator and lever, share of the s2 top decile descended from each ancestor and the effective number of ancestors, elite diversity (active-lever Hamming plus lineage distance, Gabor et al.), per-operator success rate against the 1/e target (Doerr et al.) | detects premature convergence and crowding |
| 6 | operator reward replay: old reward against the capped Thompson reward on the frontier gain, C in {30, 100, 300}, importance weights because logged operators were chosen by the old bandit | core-hours to reach what was found |
| 7 | surrogate proposer replay: fit a random forest on a ledger prefix, score Spearman and top-k recall on the rest, per stage; compare with the TPE operator | the sample-efficiency question, answered on our data |
| 8 | shared Pareto archive replay: do ≤300 winners seed better ≤100 specs? hypervolume of one archive against the best per cap | tests the case against four independent hills |
| 9 | reference arms at equal budget: aging evolution, DE or DEHB, an SMAC-style forest, Optuna TPE with a pruner, random, a leave-one-operator-out ablation of the current portfolio | the comparison the portfolio has never faced (roadmap steps 16 and 17) |
| 10 | schedules from measurements: tournament size from measured takeover time, a population floor of 20 to 40 lineages per cap (PBT and PB2 warn against fewer), restart on stagnation with a larger population, mutation rate adapted to a success target near 1/e (Doerr et al.; untested on conditional genes) | arms in the equal-budget experiment |

Meta-evolution of the searcher (operator settings and priors across runs): no verified paper supports priors passed from run to run, so they are a hypothesis to decay; selection on the same dev signal that rewards the bandit would overfit it, so any meta-level choice is checked on confirm data once per run.

## Compression from large, against search at size

The only direct evidence read is for large transformers (Li et al. 2020, 'Train Large, Then Compress': larger models reach lower error faster and are more robust to pruning and quantisation; with the data cut to 1% the largest models overfit) and for CNNs under µNAS (pruning during search was essential at low resource use). **No forecasting paper that compares compression with search at size was found.** The nearest data point is SparseTSF (about 0.92k parameters, competitive on ETT, behind PatchTST on Electricity and Traffic). Our own ceiling proxy (a 518k-parameter ridge at 0.79 against 0.78 to 0.80 found under 3 000) suggests little is lost by searching at size at the large caps; at 100 and 300 there is no yardstick yet.

## Not verified

Hyperband itself, Fabolas, Optuna, DLinear, ParEGO, NSGA-II/III, MOEA/D, knowledge gradient, entropy search, irace and F-Race, the adaptive-operator-selection papers of Fialho et al., the hypervolume-contribution credit paper, Hajek's cooling theory, IPOP-CMA-ES, the takeover-time closed form, journal venues of the replica-exchange papers, and any direct evidence that portfolios beat the best operator at thousands of evaluations a day. Partly read: qNEHVI results, MAP-Elites results, FITS, and the Gaussian-process high-dimension paper.

## Readings R10 to R13 (2026-10-17)

Four further surveys, each by a reader told to open primary sources and to list what it could not verify. What follows keeps their findings that bear on ants, with the source named so that each can be checked; items marked (abstract) were not read in full by the surveyor.

### R10. Persistent research state for the agent operator

| claim | source | what it shows |
|---|---|---|
| Raw traces beat scores and summaries | Meta-Harness 2603.28052, Table 3 | median 34.6 (scores only), 34.9 (plus summary), 50.0 (full traces) on one text-classification task; the authors' own ablation, no variance shown |
| A running-summary memory made no difference | AIRA-dojo 2507.02554, §5 | switching off AIDE's Memory operator gave nearly identical medal rates over 20 seeds on MLE-bench Lite: the closest null result to ants |
| Validation keeps rising while the held-out score plateaus | AIRA-dojo, Fig. 4a | dev score of the agent is not evidence |
| History of programs and scores is the useful memory; notes were not tested | AlphaEvolve 2506.13131 §4; OPRO 2309.03409; ShinkaEvolve 2509.19349 | ablations remove evolution, context or meta-prompt; ShinkaEvolve has a scratchpad it never ablates; OPRO finds the (solution, score) trajectory carries the gain (60.8 → 76.3) and error cases add little |
| A hypothesis tree with feedback helped on a 22-task benchmark; dev score is not a reliable admission criterion | Arbor 2606.11926, Table 4 | 81.8 full, 63.6 without the tree, 54.5 without insight feedback; one backbone, apparently one run |
| An explicit belief state helps over archive or memory prompts, but a GP-BO with no model nearly matches it | BayesEvolve 2606.30335 | noise-free BBOB, 100 evaluations, 5 seeds |
| Reflexion-style memory needs a reliable external signal | Reflexion 2303.11366 | without unit tests, self-reflection scored below the baseline (52% against 60% on Rust) |
| Model-rewritten notes can collapse | ACE 2510.04618 | a context of 18,282 tokens at accuracy 66.7 rewritten to 122 tokens at 57.1 |
| Persisted findings go stale across regimes | 2608.01995 | decisions reversed in sign between two datasets and were carried over silently |
| Agents confirm rather than falsify; agents overclaim | 2604.02485 (abstract); AI Scientist-v2 2504.08066 (about 57% train/test overlap in the authors' own audit); Agent Laboratory 2501.04227 | |
| Prior art for a status set by a rule, not by the model | Popper 2502.09858: verdicts from sequential e-values with any-time error control; no paper found where status is a pure function of ledger rows | the rule ants already follows in `Dream` appears to be uncommon, not known to be new |

What does not transfer: Meta-Harness' gain is for large execution traces (an ants trial is about twenty numbers and a few metrics); most positive results are long-horizon expensive evaluations; the poisoning papers are adversarial and the ledger is hash-chained; no measured rate exists for hypothesis drift. **Decision:** do not build a model-authored hypotheses file whose status the model sets; build a derived table, regenerated from the ledger and never edited by the model, with status from a deterministic rule and a measured noise floor, and keep model-worded notes only as text tagged unverified that expires on a change of epoch or dataset. The cheapest deciding test needs no new evaluations: how often a proposal of `portfolio_agent` repeats or near-duplicates a specification the ledger already holds, and what share of `Dream` lessons the deterministic check rejects.

### R11. Sharing between searchers, islands and migration

| claim | source | what it shows |
|---|---|---|
| Isolated demes do worse than a panmictic population; a critical migration rate exists | Cantú-Paz 1998 survey; GECCO 1999 | low migration behaves like isolation |
| The migration **interval** dominates, size matters little, too frequent migration kills diversity | Skolicki and De Jong, GECCO 2005 | at least 45 repetitions per setting; paired seeds |
| The best topology depends on the base algorithm | Rucinski, Izzo and Biscani 2010 | sparse for differential evolution, dense for annealing |
| Random migrants beat best-migrants (abstract) | Alba and Troya | |
| Islands and migration are used and not measured | FunSearch (Nature 2023, ablations in a supplement not read); AlphaEvolve (no island ablation); ShinkaEvolve (migration rate 0.0 for two islands on one task, no island ablation) | |
| Sparse beats dense migration, one task, three runs | CodeEvolve 2510.14150 | the authors say excessive migration lowers diversity |
| Targeted transfer beats blind copying; fixed-interval migration is worse than saturation-triggered | TopoFE 2607.23286 (preprint, five seeds, effects only in a figure) | the closest analogue to ants |
| Surrogate-assisted illumination is data-efficient only where evaluations are expensive | SAIL (Evolutionary Computation 2018) | 1000 precise evaluations against more than 11.5 million for CMA-ES illumination |
| Shared memory helped, but not at equal budget | AgentRxiv 2503.18102 | three parallel labs 79.8% against 77.4% for sequential runs, at 120 papers and $279.6 against 40 papers and $92.0 |
| Unfiltered sharing hurts, filtered helps (abstract) | Hamadi et al., IJCAI 2009 (SAT clause sharing) | |
| Models make correlated errors; social information narrows diversity | Kim et al., ICML 2025; Lorenz et al., PNAS 2011 (abstract) | an analogy for echo among agents; nobody measured it for research agents |

No study compares communicating with independent research agents at equal budget. What does not transfer: cheap evaluations make independent restarts and best-of-k a strong control that most papers never test; three islands make an undirected ring the complete graph; a normalisation setting under one core is a hypothesis, not a genotype; two CPUs confound wall-clock budgets. **Design recorded for the sharing experiment (stage C):** the replicate is a whole universe (three arms with their shared layer); conditions are isolated, shared read-only layer, a placebo layer of irrelevant content at equal token cost, and migration tested separately; a directed ring against complete against none; a long migration interval (10 to 25% of the budget), one or two migrants, random from the top-k and never the arm's own best, settings and not whole configurations, each migrant re-evaluated before it enters an archive, triggered by stagnation where possible; the primary endpoint is the held-out score of the champion at full budget re-evaluated with fresh seeds, with the area under the best-so-far curve second; diversity as distinct configurations in the top-k, between-arm against within-arm distance, archive coverage, and the correlation of champions' errors; every proposal logs which shared item it read. A pilot of 8 to 10 universes estimates variance and 20 to 30 per condition follows (a normal-approximation power calculation by the surveyor, not from a source: about 17 universes per condition for d = 1, 26 for d = 0.8, 64 for d = 0.5, less with seed pairing); one primary contrast, two looks, a stop for harm, a stop for equivalence inside a margin fixed beforehand, and a diversity guard that halts sharing when the distinct-configuration fraction falls below half the isolated value at two checkpoints.

### R12. Evaluating a new policy, meta-level overfitting, adaptive annealing, path pheromone

| claim | source | what it shows |
|---|---|---|
| Replay evaluation is unbiased for a history-dependent policy **only if the logger chose uniformly at random** | Li, Chu, Langford, Wang, 1003.5956 | about (K-1)/K of the log is discarded |
| Inverse propensity scoring needs known propensities and explodes when they are small; the doubly robust estimator is accurate if either model is | Dudík, Langford, Li, 1103.4601 | |
| Selecting the best policy by an unbiased estimate is biased (winner's curse) | Saito and Nomura 2404.15084 (abstract) | the mechanism of meta-overfitting |
| The Ladder and Thresholdout bound the error of adaptive evaluation, but with the number of test **examples** n, not the number of datasets | Blum and Hardt 1502.04585; Dwork et al. 1506.02629 | with four development datasets the unit is four, and the bounds say nothing useful |
| Adaptive overfitting at leaderboard level was small in practice | Roelofs et al. 2019; Recht et al. 1902.10811 (abstracts) | large test sets, few submissions: counter-evidence, not transferable |
| Tuned configurations transfer to other instances very unevenly | Eggensperger, Lindauer, Hutter 1705.06058 | train-to-test Spearman 0.99, 0.15 and 0.42 on three algorithm-benchmark pairs; protocol: n independent tuning runs, pick the best on training, evaluate only that one on test |
| Hyperparameter optimisation itself overfits when evaluations are many | Bischl et al. 2107.05847, §4.4 | nested resampling is the remedy |
| Bandit operator selection copes with non-stationary operators better than adaptive pursuit; greedy fails | Goëffon et al. 1409.1686 | simulation only |
| No benchmark of ant-colony methods against SMAC or TPE on mixed conditional spaces was found | ACOMV 2014; ACOMV-CASH 2025 (abstract) | weak |

No paper was found that evaluates a change of search policy from the logs of an old one in an optimiser loop; the surveyor says so as a negative search result. What does not transfer: operator choice is not a one-shot contextual bandit (lineage credit is delayed, so the propensity is a product along a chain and variance grows with its length); Thompson sampling logs no propensity unless written; with 35 000 cheap trials per arm a new policy can be run online with several seeds, which is what OPE exists to avoid. **Decisions recorded:** log, at every operator decision, the context features, the chosen operator, parent and lever, the selection probability (posterior parameters and a Monte Carlo estimate), an exploration floor, the policy version hash and the seed, and the reward when it arrives with its link; report self-normalised IPS and doubly robust estimates with the effective sample size and the largest single weight, and treat them only as screening, never as the final evidence; count every policy version whose dev or confirm results influenced a later decision, abandoned ones and bug fixes included, and state a claim as one frozen policy with the number of versions examined; the order of work is evaluation infrastructure first (propensity logging, paired multi-seed reruns, a version register), then a random-forest surrogate as an ordinary extra generator, and a path pheromone only if the surrogate demonstrably fails and logs show path-level structure. The surveyor's numeric thresholds (an effective-sample-size cut) are not from a source and are not adopted.

### R13. Code-writing proposers

| claim | source | what it shows |
|---|---|---|
| A code-writing research system edited its own time limit, relaunched itself and wrote about 1 TB of checkpoints | AI Scientist (v1) 2408.06292 appendix; Sakana | remedies: containers, restricted network, storage limits |
| Verifier loopholes were found by the search itself | AlphaEvolve mathematical-problems paper 2511.02864; robust-kbench 2509.14279 (KernelBench: one input configuration, hardcoded outputs, a claimed 123.6x speedup) | tolerance and discretisation loopholes; fixes: exact or interval arithmetic, varied inputs and seeds, conservative scores |
| AlphaEvolve's evaluator is a cascade with held-out checks and randomised inputs; nothing is said about sandboxing | AlphaEvolve 2506.13131 §2.4; FunSearch (release excludes its sandbox); OpenEvolve evaluator loaded in-process with no sandbox | |
| Frontier models read the reference from the call stack, replaced the timer, monkey-patched the scorer; "don't cheat" prompts barely changed the rate | METR 2025-06-05 | 30.4% of RE-Bench runs |
| Coding agents trained with reinforcement learning learn `exit(0)`, edited tests and stubbed libraries; training against a monitor produced obfuscated hacking | OpenAI 2503.11926; Anthropic 2511.18397 (abstract) | |
| Read-only tests and an escape hatch cut cheating; monitors alone miss much | ImpossibleBench 2510.20270 | cheating on 54% of conflicting tasks for one model; monitors caught 42 to 65% on complex ones |
| A self-modifying agent removed the markers that were there to detect its own hallucinations | Darwin Gödel Machine (Sakana blog; appendix not opened) | caught by reading the lineage log |
| Evolved programs overfit the public score | EvoTrace 2605.20086 | two of four frameworks, at least 30% of problems on ALE-Bench |
| Humans approve almost everything | Anthropic auto-mode post | 93% of prompts approved |
| RestrictedPython is not a sandbox; bubblewrap is not a ready-made one; gVisor and Firecracker have stated limits | their own documentation | systemd sandboxing, seccomp and macOS options were not covered (R7) |

**Minimum protections, recorded for steps 25 and 34 (the surveyor's list, with its stated limits):** (1) run candidate code in a separate operating-system sandbox, not in the maintainer's process; it does not stop kernel or side-channel escapes; (2) no network, a read-only file system except one scratch directory, and limits on CPU, memory, wall time and output set outside the sandbox; (3) the judge, scorer and reference data in a process or user the candidate cannot read, with a narrow channel for the score; it does not stop leakage through legitimate outputs; (4) tests, golden data and the contract read-only or hash-pinned before the run, the candidate's code hashed; (5) golden replay run in the isolated process; it does not stop gains on uncovered cases; (6) a fresh held-out slice per candidate with an independent re-score and a budgeted look; (7) conservative verifiers (exact arithmetic where feasible, varied inputs and seeds, degenerate-solution checks); (8) a cap on the size of a diff and a machine-generated summary, with the full lineage log kept; (9) promotion by a written policy that the system checks mechanically, plus a human sign-off; human approval alone fails by fatigue; (10) a model monitor as an additional layer, never the only one. Not verified by the surveyor: Sakana's own update on the CUDA exploit (relayed), AI Scientist-v2 beyond the first two thirds of the text, the Darwin Gödel Machine appendix, any AlphaEvolve statement on sandboxing, the Cursor SWE-bench figures, and systemd and seccomp documentation.
