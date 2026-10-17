# Drift audit: what separates 0.6.2 from the 0.6.0 that is running (2026-10-17)

Question from the owner: has the system moved too far from the one that is running, or contain changes that are not reasonable? Method: `git diff v0.6.0..<0.6.2 tip> --stat` and a reading of every behaviour-bearing hunk (campaign, history, portfolio, proposers, agent, llm, ledger); the five files in the evaluator's `code_paths` compared by hash; the arm definitions recomputed; the proposals of the pinned arm compared with 0.6.0's.

## What is the same

| thing | how it was checked | result |
|---|---|---|
| the evaluator (`gyms/tinyts.py`, `metrics.py`, `data.py`, both catalogs) | code fingerprint `e6a26c82e05dad1e`, computed on the server and on this tree; a test pins it | identical, so epoch `5da6eeee6527` is unchanged and results of old and new code are comparable |
| the definition of `portfolio`, `portfolio_llm`, `portfolio_agent`, `portfolio_dream` | arm definition hash of `portfolio` (`67618a671563`, recorded by the server in `daemon_started`) recomputed; the operator allocation of the pinned portfolio on a fixed history compared with 0.6.0's | identical. A commit that was swept in with documentation had changed `build_portfolio`'s defaults (three operators dropped, a reserved pair, a discounted bandit); it was pinned away from these arms and kept as the new arm `portfolio_lean` |
| what is proposed, evaluated, promoted by the plain arms | same operators, same bandit, same quantile rule; the new `operator_alloc` events only record decisions | unchanged |
| the data and the sealed/confirm roles | no catalog or data change | unchanged |

## What is new, and what it can do to a running arm

| change | touches a running arm? | risk | protection |
|---|---|---|---|
| interface: epoch-aware readers (`History.for_arm`), cumulative leaderboard, System page, catalog lookup that skips an unreadable directory (`home.find_catalog`) | readers only; the agent operator's read-only tools also use the epoch-aware history | none for single-epoch ledgers; for a ledger that spans epochs the readers now show the newest epoch only (by design) | tests; `data.locate_catalog` untouched because it is in `code_paths` |
| `operator_alloc` and `screen_decision` events | extra events in the hash chain (one per allocation, about one per four trials) | ledger growth of a few percent | events are chained and verified like the others |
| ledger schema v2 (`llm_calls`: `call_id`, `attempt`, `arm`) | the first opener migrates the file; the old daemon, still running, keeps working (its inserts leave the new columns empty); a ledger written by v2 refuses to open under 0.6.0 (`LedgerTooNew`) | one-way: downgrading needs the pre-migration copy | forward-only migration with a safety copy; rollback procedure in [upgrading](../upgrading.md) |
| model-call reliability (breaker, retry, budget) | the two model arms behave differently from their first restart: fewer wasted calls (the pilot spent its daily cap on endpoints that fail 83 to 100% of the time) | it changes the experimental condition of any model arm | the model arms of the pilot are sealed; model arms run only in a later, separate series |
| definition guard (`ants run` exits 4 on a changed arm definition) | refuses a restart whose arm definition differs from the ledger's last start | a legitimate change must be acknowledged with `--new-definition`; the unit does not restart-loop on exit 4 | the pinned hashes make an unchanged arm pass; tests |
| series runner, sealing bundle, experiment registry, pull-backups, soak and drill scripts | new commands and units; nothing runs unless started | the drill kills a service by design | explicit arm name, refuses `all` |
| learned screen (`portfolio_screen`) | a new arm only | none for other arms | its settings are part of its own definition hash |

## What to be careful about

1. A change that moves nothing today can still move a running arm at its next restart (a systemd watchdog restart, a reboot): code on disk is what the next start runs. That is why the pin test compares the pinned arm with 0.6.0 and why deploying is: seal, then install, then start under the new registry, not install over running arms.
2. Platform: a whole-search sequence differs between machines (three CI machines drew three sequences from the same code); equality of two code versions is therefore tested on a pure-Python allocation, not on a full search.
3. Anything in `code_paths` is an epoch: [roadmap](../roadmap.md) step 48 describes the proof that would let a performance-only change keep its epoch.
