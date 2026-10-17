# Roadmap

What can change next, in order, what each step needs and which ones start a new epoch (a new measuring protocol: results before and after are not compared). Status is dated. Everything here is a plan; the evidence it rests on is the first eight hours of three searches on the server (2026-10-13), analysed from copies of their ledgers.

## Where things stand

| | |
|---|---|
| shipped | ledger, runner, campaign, search portfolio, sealed-look accounting, chain anchors, verified backups, onboarding, service units with watchdog, terminal interface, agent operator, update check (versions 0.1 to 0.5) |
| running | `portfolio`, `portfolio_llm`, `portfolio_agent` on a 2 vCPU / 2 GB server, one seed each |
| learned | the language-model operator of `portfolio_llm` produced two evaluated trials in eight hours (the bandit starved it, so that arm was the plain portfolio); the 100 and 300 parameter caps were almost never searched (the promotion cut was global); stage s2 agrees with s1 at Spearman 0.99 and costs 45% of the CPU; stage s0 is a coarse filter at best; arms differ in speed because the `ens` lever triples evaluation cost |

## Order of work

| # | Step | Claim it tests | Cheapest experiment | Cost (core-days) | Needs | Epoch | Status |
|---|---|---|---|---|---|---|---|
| 1 | Per-cap promotion; a guaranteed slice for the model operators; a yield event per model call | the small caps hold models no one has looked at; the model operators were never tested | restart the three arms in the new epoch | 0.2 | done in 0.5.0 | yes (contract `promote_by_cap`) | built, deploying |
| 2 | Question 9: `portfolio_agent` against `portfolio_llm` and `portfolio` | reading the ledger beats a summary of it | 8 seeds per arm, 150 deepest-stage evaluations each, same seed list, interleaved | 3.3 | seed runner; fix N and seeds in a file first | no | next |
| 3 | The control for 2: the agent given random ledger rows | the gain comes from reading failures, not from extra context | one more arm in 2 | 0.4 | an `agent_random` arm | no | planned |
| 4 | Random search at 2× budget, TPE alone | does the portfolio beat the plain baselines (principle 6, question 1) | arms in 2 | 0.5 | nothing | no | planned |
| 5 | Proxy fidelity on an unselected sample | what share of the s2 top quartile does s0 keep (question 8, unbiased) | run about 30 s0-rejected specs through s1 and s2 | 0.05 | a script | no | planned |
| 6 | A stage after s1 that is not a copy of it | s2 is redundant (ETTm1 is the same family as ETTh1) | replace s2 by a non-ETT family or more folds | 0.5 | catalog entry, contract | yes | after 2 |
| 7 | Tuned hand-built baselines (SparseTSF, FITS, DLinear, ridge) | the frontier claim of question 1 | in-house runs on the same windows | 0.5 | baselines | no | planned |
| 8 | Dev-to-confirm gap of the finalists | does the search overfit the four dev datasets | confirm looks, counted | 0.05 | gate call | no | after 2 |
| 9 | Cost-adjusted reporting | arms with `ens=3` are not cheaper or dearer by accident | report best per CPU-second next to best per evaluation | 0 | analysis | no | planned |
| 10 | The agent proposing structure only | mechanism beats parameter tweaks once the portfolio has converged (MH3) | a prompt switch in 2 | 1.3 | prompt flag | no | deferred |
| 11 | Parent rules (greedy, restarts, aged eviction) | question 3 | arms | 2 | arms | no | deferred |
| 12 | A second arena with parameters shared across datasets | question 5 | a new gym | 5+ | large | yes | deferred |
| 13 | Academic-compatible report (Tier A): per-dataset, per-horizon MSE/MAE on the standard LTSF protocol for frozen candidates | our numbers can be read next to the literature's | a gym `ltsf` profile that emits per-dataset MSE/MAE | 0.3 | [benchmark §13](benchmark.md) | no (report only) | planned, after 2 |
| 14 | In-house references under the same harness: DLinear, SparseTSF, FITS, PatchTST, ridge, naive floors, optionally one zero-shot foundation model | where we stand against what the field uses, at equal tuning budget | reference harness | 1 to 2 | step 13 | no | planned |
| 16 | Standard search baselines as reference arms: Optuna TPE with a Hyperband pruner, SMAC, NSGA-II, aging evolution, random | is the portfolio worth its complexity | arms in step 2, same budget | 1 | thin wrappers (optuna and smac are optional extras) | no | planned, with 2 |
| 17 | Leave-one-operator-out ablation of the portfolio; operator-only arms | which of the nine earn their place | arms | 1.5 | nothing | no | planned, with 2 |
| 18 | Search redesign: ASHA rungs, aging evolution plus one surrogate proposer, a Pareto archive with MAP-Elites cells, re-evaluation of the top | the standard shape does at least as well with fewer parts | [search.md](search.md) | 3 | build after 16 and 17 | yes (stages) | proposal |
| 19 | Self-adapting and cross-run evolution of the searcher (operator settings, priors passed from run to run) | the searcher improves without a person | [search.md](search.md) | 2 | after 18 | no | proposal |
| R | **Research phase** (read before building 18 and 19): six literature surveys with a decision memo, [research/search-landscape.md](research/search-landscape.md): R1 fitness-landscape analysis and landscape-based algorithm selection; R2 multi-fidelity and multi-objective Bayesian optimisation (Hyperband family, BOHB, DEHB, SMAC, expected hypervolume improvement); R3 evolution, annealing and replica exchange as selection schedules (aging evolution, self-adaptation, island models, parallel tempering, population-based training); R4 quality-diversity (MAP-Elites, CMA-ME) and novelty; R5 operator credit assignment (adaptive operator selection, hyper-heuristics, hierarchical bandits, racing) and weight inference (hierarchical models, fANOVA, ARD); R6 search under tight memory and size budgets (tiny-ML architecture search, compression of large models) | which established methods fit a conditional, noisy, cheap-to-evaluate, two-objective landscape, and in what order to adopt them | read primary sources, no code | 0 | an owner for the memo | no | in progress |
| 21 | Landscape analysis of the ledgers we already have: ruggedness along lineages, neutrality (levers that change nothing), lever importance and interactions, how many distinct basins the finished runs reached, rank correlation between stages per cap | aim 3, from data, with no new compute | an analysis module and a page in the interface | 0.1 | R2, R1 | no | planned, first after R |
| 22 | A replay harness: fit a surrogate on a prefix of a ledger and score how it ranks the rest; replay a proposal policy against recorded trials and count core-hours to reach what was found | aims 1 and 4: test new policies offline before spending compute | analysis module | 0.2 | step 21 | no | planned |
| 23 | Compression from large: train larger gradient-based models (more parameters, more steps), then quantise, prune and distil them to each cap and compare with the closed-form search at that cap | does shrinking a big model beat searching at size | a gradient gym profile | 3+ | after 2 and 20 | yes | later |
| 20 | Direction report (lever importance, exhausted regions) and a training hand-off (refit winners with more data; optional gradient fine-tune) | what the search teaches, and what a model gains from real training | analysis, a gym profile | 1 to 2 | after 2 | no | planned |
| 15 | TinyTS-Bench v1: three tiers and one pre-registered family-weighted composite; `ants bench` re-runs it anywhere | one reproducible conclusion, not a pile of tables | `bench` command, pre-registered weights | 2 to 3 | steps 13, 14, 8 | no | planned |

## Adjacent problems, in the same plan

| problem | why it matters | change | priority |
|---|---|---|---|
| a limit on how long a search runs (evaluations, deepest-stage evaluations, CPU hours, a plateau rule), recorded and extendable only by a person; a model may advise, never decide | an unbounded search burns budget without saying when to stop | `gate.toml` per workdir, `gate_closed` / `gate_extended` events, `ants gate`; written once and set aside, to be rebuilt and tested | high |
| an automatic polish stage: when a new best appears at a cap, a short deterministic burst of single-lever refinements around it (prune ratio, quantisation, distillation weight, ridge, ensemble size) | prune/quantise/distil are levers in the search today, never a post-selection step that triggers itself | a `polish` operator with a burst trigger on a new best; a new arm definition, so a fresh arm | medium |
| no gradient or deep post-training: models are fitted in closed form | the 'post-training' of the task description is only levers | decide whether a small gradient-trained arm (low-rank adapters) belongs in the same cap; separate gym, not a lever | later |
| fewer wasted steps: snap jitter-only levers to a grid in the spec key | 35 to 45% of proposals were coarse duplicates | `Space.key` change; changes which specs count as seen, so a new epoch | high |
| more than one seed, and how often runs agree on the peak | the noise floor and the number of basins | step 2 | high |
| ceiling references per cap and a headroom line on the home screen | 'how far from the top' has no answer today | step 7 plus an interface field | medium |
| a wider development set (a non-ETT family, a seasonal family, an epidemic family) as a new epoch | s2 repeats s1; four datasets from three families is thin | step 6; confirm and sealed stay out | high |
| latency, memory and on-device checks of the finished models (ONNX parity, fixed-point) | the case for tiny models is deployment; no number backs it yet | the stubbed artifact checks (E3) | medium |
| cost-adjusted reporting (best per CPU-second next to best per evaluation) | arms differ in speed because of the ensemble lever | analysis only | medium |
| multivariate and cross-channel structure | the models are channel-independent by construction | a new lever family or gym | later |
| probabilistic forecasts (intervals) | point errors hide calibration | a metric and a gym profile | later |
| the experiment registry, a seed runner, retention and off-machine copies | arms are started by hand today | see "Experiments, arms and seeds" | high |
| a language-model review at checkpoints that proposes continue or stop | a person should not have to read every plateau | advisory event only; the deterministic gate stays the authority | later |

Dropped for now: a red-team proposer (adds attack surface before the basic comparison exists), operator-code search and an agent that proposes cascade changes (the evaluator is the one thing the design keeps fixed; a human-approved lane with a new epoch is the only version consistent with the principles), velocity and acceleration predictors (the ledger holds three stage values per spec, not learning curves).

## Gaps named by outside readings of the code (2026-10-17)

Readers who went through the source found these. They are accepted as stated; each has a step, and until its step is done the matching claim is not made.

| gap | what is true today | step | the claim we do not make until then |
|---|---|---|---|
| **No operating-system isolation.** Evaluation processes run under the daemon's OS user; the boundary is application-level, not a proven sandbox | roles by environment and code, closed agent tools, strict parsing, fencing, hash chain, rlimits, one systemd sandbox around the whole daemon | 25 | that sealed data or the ledger is out of reach of an evaluation process |
| **The search ecology is not shown better than simple methods.** Nine operators plus a model and an agent; against Optuna TPE, random search and plain evolution nothing is measured. Observed: the cheap stage keeps about half of the true top quartile; s1 and s2 agree at Spearman about 0.997; 35 to 45% of early proposals are coarse duplicates | the differentiator today is the experiment framework and evidence control, not a better optimiser (the FAQ says so) | 16, 17, 18 | that the portfolio beats Optuna, random search or aging evolution |
| **A small parameter count is not a small deployment cost.** `count_params` estimates a budgeted parameter cost; pruning and quantisation are simulated in NumPy; no model has been exported or run on a device | the cap is a budget, not a measurement | 26 | any TinyML deployment claim |
| **E7, the final claim, is not wired.** Freeze, sealed credits and anchors exist as a library; no command freezes, seals or reports | `gate.seal` and the ledger functions are tested; no CLI | 27 | any out-of-sample claim; the sealed sets stay untouched |
| **TinyTS-Bench has no results.** The protocol is written; every cell says pending | | 2, 13 to 15 | any ranking of arms or of ants against references |
| **Unattended reliability has early evidence only.** Three arms have run for days without a restart; that is a record, not proof | the chaos tests pass on two operating systems; the server has run since 2026-10-13 | 28 | 'runs unattended for weeks' |
| **Figures reproduce; results do not (yet).** The SVGs rebuild exactly from `frontier.json`; the ledger behind it and the newer server ledgers are not published; the raw series are fetched by hash, not shipped | `ants data fetch` verifies hashes; chains and anchors exist | 24 | that a reader can re-check a number from a clone |

| # | Step | Claim it tests | Needs | Epoch | Status |
|---|---|---|---|---|---|
| 24 | **A results bundle.** Per published result: the compressed ledger (scrubbed of anything but specs, metrics and events), the chain head and anchors, the data manifest, the ants version and package hash, and a command that verifies it: `ants verify-bundle` checks the chain against the anchors and recomputes `frontier.json` and the figures from the ledger; `ants reverify --sample N` re-evaluates N random trials from their specs and compares the metrics bit for bit (the gym is deterministic) | a number in the README can be re-checked by a stranger | a release asset per result, two commands | no | planned, before any result is quoted outside the repository |
| 25 | **Operating-system isolation of evaluation.** Levels: (1) each evaluation as its own transient service or user: no network, a private tmp, a read-only view of the code, only the development datasets mounted (confirm and sealed absent, so the roles become physical), the ledger and the key directory not visible, a cgroup for memory and CPU, `NoNewPrivileges`, a system-call filter; (2) the same with unprivileged namespaces or Landlock where systemd units are unavailable (local installs), with macOS handled separately; (3) a stronger boundary (gVisor or a micro-VM) only if code written by a model is ever executed. Acceptance tests run from inside an evaluation: reading the ledger, a sealed file, the key file, opening a socket and writing outside the trial directory must all fail | the application-level boundary is backed by the operating system | research R7, a runner change | no | planned; a precondition for any code-writing proposer (operator-code search, the maintainer) |
| 26 | **Deployment cost, measured.** Export the finished models (ONNX, and a plain reference runtime), then measure on stated hardware: latency (median and 99th percentile), peak memory, file size, energy if the device allows, and int8 against float32 agreement (maximum absolute difference, error change); report them next to the parameter count, per cap. Pruning and quantisation move from simulation to the exported artefact | 'tiny' means cheap to run, not only few parameters | export, a device (a Raspberry Pi class board, a phone, optionally a microcontroller) | no | planned (E3) |
| 27 | **Wire the final claim (E7).** `ants freeze` records the candidate set and the protocol, `ants seal` makes the one counted look, `ants claim` writes the report with the chain head, the epoch and the credits used; refusal when anything is out of order | an out-of-sample statement is produced by the tool, not by hand | CLI over `gate`, `report` | no | planned, after 2 |
| 28 | **Reliability evidence.** A stated soak target (for example 30 days, restarts counted, watchdog events counted, a daily integrity and chain check, backup success every day, one drill each of kill -9, a full disk, a network outage and a model endpoint outage), logged in `DEPLOY.log`-style and published as a dated table | 'runs unattended' is a measurement | a drill script, a log | no | planned; the clock started 2026-10-13 |

## Experiments, arms and seeds: what is managed and what is not yet

| managed today | not yet (and the change) |
|---|---|
| epochs are recorded per trial; `ants epochs`; results of different epochs are never pooled | **an experiment registry.** Arms are started by hand (`remote_deploy.sh start <arm>`); which arm means what, with which seed and for which question, lives in this document and in people's heads. Change: a `deploy/experiments.toml` (name, arm, seed, contract, the question it answers, status) that `remote_deploy.sh start` reads and `ants doctor` can check against the ledgers |
| every start records version, epoch and the arm definition; a changed definition inside one ledger raises a warning | **seeds.** One seed per arm. Change: `ants run --seed` already exists; a runner that works through a seed list in order on the free core and writes each into its own workdir (`portfolio_s1`, `portfolio_s2`, …) |
| a deploy backs up every ledger first, never touches `/var/lib/ants` | **retention.** Old runs are moved to `archive/old-runs` by hand (40 MB so far). Change: a documented keep-forever rule for ledgers and anchors, a size report in the interface, and a cold-copy step (`pull-backups` on a schedule from a machine that is not the server) |
| backups are verified and listed; restores refuse to go back in time | **off-machine copies.** `pull-backups` exists and has not been run by anyone yet |
| the arm name picks the workdir (`/var/lib/ants/<arm>`) | **naming.** The rule (never reuse a name for a different definition) is in [upgrading](upgrading.md); nothing enforces it except the doctor warning after the fact. Change: refuse to start an arm whose definition hash differs from the ledger's last `daemon_started`, unless `--new-definition` is given |

Training results are kept in three places: the ledger (every trial, spec and metric, chained), the per-trial files, and the verified backups; the first epoch's runs are in `/var/lib/ants/archive/old-runs` untouched. What a harness change can do to them is in [upgrading](upgrading.md). Whether the new epoch is better than the old one is not known yet: the changes were made because the first epoch had not searched two of four caps and never ran the model operator, so the two epochs answer different questions; comparing their best values would compare protocols, not searches.

## How the comparison is kept honest

1. N, the seed list and the test are written to a file before the runs start; seeds are interleaved across arms; nobody looks at the comparison until all runs finish.
2. Primary metric: best rel_mse at 150 deepest-stage evaluations in the ≤1000 and ≤3000 bins; paired Wilcoxon per seed, agent against llm and agent against plain, Holm correction; medians and a bootstrap interval over seeds.
3. Between-run spread from three data points is about 0.0024 in best rel_mse, so differences of 0.003 need about 10 seeds and 0.005 about 4; the plan uses 8.
4. Report equal evaluations and equal CPU; stop a run only for infrastructure faults (model yield under 5% of proposals, an endpoint down), never for efficacy.
5. A change to promotion, stages or data is a new epoch; results of different epochs are never pooled (`ants best`, `top` and the interface read the current epoch only).

## What can be changed, and how

| To change | Edit | Starts a new epoch? | Test that guards it |
|---|---|---|---|
| the promotion rule | contract `[cascade]` (`promote_quantile`, `promote_by_cap`, `min_history`) | yes, the contract is hashed | `tests/test_agent.py` (by cap), `tests/test_campaign.py` |
| stage timeouts and the memory cap | contract `[budget]`, `[objective]` | yes | `tests/test_contract.py` |
| the model operators' share | `portfolio.LLM_FLOOR` | no; it defines the arm, so start a fresh workdir | `tests/test_agent.py` |
| the evaluator, metrics, data | the gym files listed in `code_paths` | yes | `tests/test_tinyts.py` |
| the interface | `src/antslab/tui/` | no | `tests/test_tui.py` |
| a dataset | `data/catalog.toml`, `data/sources.toml`, the pinned manifest | yes | `tests/test_data.py`, `ants data verify` |

How to add an operator, a gym, a data source, a ledger migration and an interface screen: [gyms](gyms.md), [contracts](contracts.md), `data/sources.toml` (one `[[source]]` per file, hash into `data/MANIFEST.sha256`), `ledger.MIGRATIONS` (append a step, bump `SCHEMA_VERSION`, never edit an old one; the first migration of existing data keeps a full copy), [tui](tui.md).
