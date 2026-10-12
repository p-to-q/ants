# E4 Trajectory - is this research path worth more budget?

A **path** is a skeleton niche (the discrete structural choices that define a research programme, e.g. core x norm x decomposition x pre-training).
Judging a path by its current best (*level*) is the greedy mistake; this layer judges its **kinematics**.

* `level`: best-so-far of the path.
* `velocity`: relative improvement per evaluation over the last `w` evaluations.
* `acceleration`: change of that velocity against the `w` before it (positive = improving faster).
* `potential` (the prospect of a path): 1.0 while it has fewer than `w + 1` = 16 evaluations; afterwards `20*velocity + 20*max(0, acceleration)`, >= 0.
* labels (`landscape.niche_table`): **young** (fewer than 16 evaluations), **leader** (best level), **riser** (velocity or acceleration above 1e-4), **stalled** (otherwise).

Used only to *allocate exploration* (QD parent choice, DE niche choice), never to rank the final answer - that is level on E1/E2.
Implementation: `history.trajectory`, `history.potential`, `landscape.niche_table`. A leader is not necessarily the path that will
produce the next champion; this layer is the explicit test of that claim (`lineage.stepping_stats`).

Status: computed from first-stage records of one `History`. Under `ants run` it is epoch-filtered; the board and `ants top` build their own unfiltered history. Whether velocity and acceleration actually predict later success is untested ([open question 4](../open.md)); `potential` currently steers QD parent choice and DE niche choice only.
