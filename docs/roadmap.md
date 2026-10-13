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

Dropped for now: a red-team proposer (adds attack surface before the basic comparison exists), operator-code search and an agent that proposes cascade changes (the evaluator is the one thing the design keeps fixed; a human-approved lane with a new epoch is the only version consistent with the principles), velocity and acceleration predictors (the ledger holds three stage values per spec, not learning curves).

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
