# E6 Lab health - is the lab progressing, and can we trust its measurements?

**Emits:** continue / pivot / widen the domain / call a human. Slow and global. **Partly implemented:** the machine checks of `ants doctor` run; the progress and calibration parts below are library functions or not run.

| item | status | where |
|---|---|---|
| hash chain intact, disk level, no stalled queue, failure rate over the last 200 trials, runners past their lease, archive backlog, LLM parse rate, number of epochs | **run** by `ants doctor` (exit 2 on FAIL; `ants-health.timer` runs it every 15 min on the server) | `ui.doctor` |
| ruggedness (parent/child correlation), plateau fraction (inert levers), retired discrete values, stagnation length, niche labels | computed by `landscape.diagnostics`; the board shows ruggedness, stagnation length, niche labels and stepping-stone statistics (plateau fraction and retired levers are not displayed) | `landscape`, `board` |
| frontier area: hypervolume of the (log10 params, rel_mse) frontier against a reference corner, plotted over time | function exists (`metrics.frontier_area`), not called by any loop, report or board | `metrics` |
| validated gains per CPU hour, discovery throughput | **not run**: no code computes them; `validated` is a registry state and nothing in production reaches the registry | - |
| proxy fidelity: Spearman between cheap-stage and deep-stage rankings | **not run**: no code or test computes it | - |
| judge calibration: ECE, Brier, AUC of every logged prediction against its realised outcome | functions exist (`judge.calibration`, `authority`, `resolve_triage`); no job runs them and no judge is attached to the loop | `judge` |
| anchors, state-vs-event audit of the `trials` table, `Registry.verify()` | **not run** by `doctor` | `ledger`, `registry` |
| the decision itself (continue / pivot / widen / call a human) | **not run**: a human reads the output | - |

`ants doctor` reads one arm of one workdir. Its epoch check only warns when more than one epoch is recorded.
