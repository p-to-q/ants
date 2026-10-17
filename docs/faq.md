# FAQ for a skeptical reader

Short answers, each pointing at a file or a test. Where the honest answer is "we do not know yet", it says so and links [open.md](open.md).
Numbers come from the repository or from commands run while writing these pages (marked "measured here", macOS arm64).

## 0. Does the nine-operator portfolio beat simpler search?
Not shown. Nothing has been run against Optuna's TPE with a pruner, random search at twice the budget or plain aging evolution, and in the first measurements all top specs came from evolutionary mutation and crossover. What ants offers today is the experiment framework and the control of evidence, not a proven better optimiser; the comparison is roadmap steps 16 and 17.

## 1. Why not just use Optuna?
You can: `ants run --arm tpe` is Optuna's TPE on the same space, same evaluator, same ledger (`proposers.TPEProposer`; extra `search`). TPE is also one
operator inside the portfolio (`portfolio.TPEHistProposer`). ants is not a better optimiser claim. It is the harness around one: a fixed evaluator proposers cannot edit,
a hard cap as a constraint, epochs, a ledger, a sealed final look. Whether the portfolio beats TPE or random search at equal budget is **unknown**; `surfaces.md` says
"no claim", and [benchmark.md](benchmark.md) is the pre-registered way to find out. Caveat found while writing it: TPE here learns only from s0 values and never sees the filtered
(over-cap) proposals, so under a tight cap it proposes mostly infeasible points (benchmark.md, "Blockers").

## 2. Why not a bigger model?
Because the question is the one inside the wall: how much of an unconstrained model's skill survives at 100 to 3000 parameters. The unconstrained reference exists
and is strong: closed-form ridge, lookback 720, 518 400 parameters, `rel_mse` 0.7905 (`assets/frontier.json`; measured here 0.7905 for `norm=last, ridge=1`,
the same value). Nonlinear levers (`torch`) are a planned extra, "development machine only" (README requirements table). A larger model is not forbidden; it is a different track.

## 3. Can the language model cheat?
It can propose; it cannot score. Model output goes through `extract_json` and then `Space.validate`: a closed set of keys, strict types, inclusive bounds, no clamping, no repair
(`contract.py`; `tests/test_llm_proposers.py`, `tests/test_security_llm.py`). Hints are data and cannot change a gate or budget (`tests/test_ui.py::test_hint_reaches_the_llm_prompt_but_only_as_a_suggestion`).
The one path by which a model could touch code, the maintainer, has a path allow/deny list (ledger, gate, runner, contract, campaign, registry, metrics, judge, tests and contracts are
never patchable), an AST gate, golden replay and rollback (`maintainer.py`, `tests/test_security_maintainer.py`), and is not wired into the daemon (comment in `deploy/ants@.service`).
What this does **not** claim: protection from the operator. The gym, the contract and the supervisor run as the same OS user; a human who edits them starts a new epoch, which is visible but not prevented.
Open: a red-team proposer that tries to break this ([open.md](open.md) section 6).

## 4. What if ridge saturates the space?
Then the honest result is negative. [open.md](open.md) section 1 names the risk: a tuned ridge with cheap structure may already fill the space. Evidence so far, all on development validation:
unconstrained ridge 0.79; FITS-like at 7 296 parameters 0.8514 (measured here at s2); best s2 rows in the 61-row demo ledger: 0.892 at 300 parameters, 0.871 at 420, 0.846 at 1 190
(`runs/demo`, a short run, one seed). The benchmark defines in advance what counts as a null result.

## 5. How is the cap counted?
Analytically, at horizon 720, before any CPU is spent: `gyms/tinyts.py::count_params`. Core parameters (`period`: ceil(720/p) x floor(L/p); `linear`: L x 720; `fourier`; `basis`),
plus the trend head, plus the adapter when pre-trained, times `(1 - prune)`, summed over ensemble members. The default spec is 30 x 14 = 420. Quantisation changes `bytes`, not the count.
`tests/test_tinyts.py::test_analytic_param_count_matches_stored_weights` compares the formula with stored non-zero weights. The cap is a constraint: over-cap rows are never leaders
(`history.feasible`), are not promoted, and are kept only as a 5% stepping-stone quota (`stone_quota`).

## 6. What is an epoch?
The identity of the measuring machinery: a hash of the evaluator source (`code_paths`), the whole parsed contract and the data manifest. Every trial carries it; `History` ignores other epochs
(`tests/test_campaign.py::test_history_ignores_other_epochs`). Redeploying changed code, editing the contract (even `description`) or re-downloading data starts a new epoch. See [contracts.md](contracts.md#identity-what-changes-the-epoch).

## 7. Why a hash chain?
So a deleted or edited row is detectable: each event hashes its predecessor (`ledger._hash`; `tests/test_redteam_chain.py`). It does **not** stop someone who rewrites the whole chain or cuts its tail.
That needs a copy of the head the writer cannot edit: `Ledger.write_anchor` and `gate.seal(anchor_file=...)`. There is no CLI for the anchor yet; [operations.md](operations.md) shows the call.

## 8. What is "sealed"?
Whole data families (10 datasets, 8 families: ECL, Traffic, Household, Metro, Sunspots, ... in `data/catalog.toml`) that no search stage reads. A candidate is frozen (hash of spec + code + epoch + data protocol),
one look is spent **before** the evaluator runs, and the result is a hash-chained event (`gate.seal`, `Ledger.record_unseal`; `tests/test_redteam_credits.py`). One look per frozen candidate; limits only ratchet down.
Nothing sealed has been touched: `ants status` on the demo shows `"unseals": 0`. Caveat: the `--final` command evaluates test windows of dev, confirm **and** sealed sets together, so the sealed headline is a subset of its per-dataset keys.

## 9. What exactly may a proposer not do, and where is it enforced?

| forbidden | enforced by | test |
|---|---|---|
| a proposer editing the evaluator, contract, roles or ledger | no write path exists from `Proposal` to those files; LLM text enters via `Space.validate` | `test_security_llm.py`, `test_llm_proposers.py` |
| a contract smuggling a command or secret | argv-only commands, six placeholders, `env_passthrough` deny-list, `antslab.*`-only static refs | `test_security_contract.py` |
| search reading held-out data | catalog roles; `ANTS_ALLOWED_ROLES` role policy (set to `dev` by the campaign for every search trial, and by `gate.seal` for the final look) | `test_redteam_roles.py` |
| a second sealed look | credit spent first, counted from the chain, ratchet | `test_redteam_credits.py` |
| evaluator code changing after freeze | code fingerprint checked in `gate.seal` | `test_ledger_gate.py` |
| a stale process overwriting a result | fencing token `attempt` in `Ledger.finish` | `test_ledger_gate.py`, kill -9 chaos test |
| an LLM-written number in a report | `report.lint_numbers` marks it UNVERIFIED | `test_report.py` |

Not forbidden: the operator changing anything (it forks the epoch), and code that the operator wrote into a gym.

## 10. What is the cascade, and is the cheap stage a good proxy?
S0 (one dataset, one horizon, one fold, 0.2 s) -> S1 (3 datasets, 36 tasks, 5 s) -> S2 (4 datasets, 48 tasks, 11 s), promoted by stage quantile. **Whether S0 ranks like S2 is unmeasured by the system.**
On the 19 specs of the demo that reached s2, the Spearman correlation of s0 and s2 `rel_mse` is 0.20; the five best at s2 had s0 ranks 14, 15, 0, 2, 13 of 19. That sample is small, one seed, and
restricted to promoted specs, so it is a warning and not an estimate. `ants top` and `ants best` rank by the deepest stage reached and print which stage; `ants show` lists every stage ([cli.md](cli.md)).

## 11. Are the README numbers results?
No. They are development validation of a short run (60 finished trials), measured at the same stage S2 as the baselines; the baselines are untuned and ants picked its best
on the same validation windows, so the comparison favours ants. Until a campaign runs against the protocol in [benchmark.md](benchmark.md), treat them as a first look.

## 12. Why `rel_mse` and a geometric mean?
It is MSE divided by the seasonal-naive MSE at the dataset's own period, so it is scale-free and below 1 means "beats the reference". The geometric mean treats a 10% gain and a 10% loss symmetrically
and each independent family counts once (`metrics.gmre`, `family_weights`; [E1](evaluation/E1-quality.md)). Weakness: it depends on the reference being a sensible forecaster on each dataset (`exchange` has no seasonality, the reference is the last value).

## 13. Is the same seed the same run?
Yes for the loop's decisions (`tests/test_campaign.py::test_same_seed_same_trajectory`) and for the closed-form gym, which has no sampling noise
(`tests/test_tinyts.py::test_deterministic`). Floating-point results can differ across numpy and BLAS builds; versions are recorded in the epoch row but not hashed.

## 14. Is "random search at 2x budget" actually 2x the compute?
Not automatically. The `random` arm draws uniformly and relies on the campaign's free filters. Measured here on `contracts/tinyts_real.toml`: of 20 000 uniform draws, 1.1% pass the memory estimate
and 0.16% to 0.43% pass memory plus the cap (100 to 3 000), so the loop discards hundreds of proposals per evaluation and sleeps 5 s whenever a refill comes back empty.
Evaluations are counted correctly; wall-clock time is not comparable across arms. See the blockers in [benchmark.md](benchmark.md).

## 15. Can it run on 2 vCPU / 2 GB for weeks?
Designed to; **not measured on the target machine**. Peak child memory measured here: 745 to 785 MiB at s2, supervisor 53 MiB, against `MemoryHigh=900M`; the memory estimate is lower than that measurement.
Details and failure table: [operations.md](operations.md).
