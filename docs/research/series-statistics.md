# Comparing stochastic search arms with few seeds: what the literature supports, and what it changed in E43 (2026-10-17)

A survey read for the pre-registration of [E43](../experiments/E43-learned-screen.md) (8 seeds per arm, one confirmatory test). Sources were fetched as abstracts or documentation; the full text of several was not read. Items marked (not verified) are the surveyor's own statement of what it could not confirm.

## Recommendations and where they come from

| recommendation | source | what it shows | use in E43 |
|---|---|---|---|
| report intervals, performance profiles, interquartile mean with stratified bootstrap intervals | Agarwal et al., NeurIPS 2021 (arXiv 2108.13264); `rliable` | point estimates mislead in the few-run regime | descriptive only: E43 has one task, so the stratified bootstrap is an ordinary bootstrap |
| randomise every source of variation; decide by the probability of improvement above a margin (Mann-Whitney with bootstrap intervals) | Bouthillier et al., MLSys 2021 (2103.03098) | comparing averages gave about 90% false negatives at k = 50 | A12 reported as the effect size |
| bootstrap-interval tests are liberal at small n; Welch's t-test recommended; n of at least 20 for preliminary studies | Colas, Sigaud, Oudeyer 2018 (1806.08295) | at n = 5 the bootstrap test had a false-positive rate of about 10% | the bootstrap interval of a median over 8 values is dropped |
| report seeds, variance, tests | Henderson et al., AAAI 2018 (1709.06560) | variance across seeds and non-determinism | validity table, per-seed curves |
| report best-found against the compute budget, disclose the budget | Dodge et al. 2019 (1909.03004) | conclusions can reverse at another budget | budget sensitivity rows (14 400, 28 800, 50 400 s) |
| separate tuning seeds from test seeds | Eimer et al. 2023 (2306.01324) | the landscape depends on the tuning seed | the pilot is excluded because it informed the design |
| runtime ECDFs, targets, expected running time with restarts; data profiles | Hansen et al., COCO (1603.08785, 1605.03560); Moré and Wild 2009 | runtime to a target is the central measure; failures are aggregated by simulated restarts | times to a target are right-censored at the budget; the restart table is ERT-style |
| Bayesian signed-rank test with a region of practical equivalence | Benavoli et al., JMLR 2017 (1606.04316) | probability statements and equivalence | the idea of a margin transfers; validity of the test for seeds on one task (not verified) |
| anytime-valid inference (confidence sequences, e-processes) | Howard et al. 2021 (1810.08240); Ramdas et al. (2210.01948) | validity under optional stopping | not needed: two fixed looks |
| pre-registration in ML | NeurIPS 2020 workshop (PMLR 148) | a protocol reviewed before results | the `e43-prereg` tag |
| probability of superiority (Vargha-Delaney A12) | Arcuri and Briand | standard for randomised algorithms | reported (the run-count recommendation attributed to them was not verified) |
| tamper-evident logs: a signed head held outside the writer | Crosby and Wallach 2009; RFC 9162 | an append-only log is checked against an external head | the anchors design; step 29 |
| package with a manifest | BagIt, RFC 8493 | `bagit.txt`, `data/`, `manifest-sha256.txt`, optional tag manifest | the sealing bundle; the BagIt layout is a candidate |

## What a simulation of the E43 design showed (the surveyor's own, 8 pairs)

* The smallest two-sided p of an exact Wilcoxon test on 8 pairs is 0.0078; the realised size at the nominal 0.05 is about 0.039.
* Difference detected 80% of the time: about **0.055** (per-run sd 0.03) to **0.09** (sd 0.05), and with the "one lineage decides the run" mixture about 0.06 to 0.10; detected 50% of the time: about 0.04 to 0.06. A true benefit of 0.02 is detected 10 to 18% of the time. By a normal approximation (not simulated) 80% power for 0.02 at sd 0.04 would need about 63 pairs, for 0.03 about 28, for 0.05 about 10.
* A bootstrap 95% interval of the median had coverage 92.5% and a mean width of about 0.09, so it never fits inside plus or minus 0.01; showing equivalence to 0.01 would need roughly 270 seeds per arm (approximation). Draft 1's "no effect" outcome was therefore unreachable and is removed.
* Pairing by seed helped only when a shared seed effect was put into the simulation; with none, the unpaired Mann-Whitney test had more power. Whether the same seed gives the same root in both arms is not verified from the code.
* The interquartile mean is a few percent more efficient than the median as a location estimate over 8 differences; not a reason to change the primary.

## What changed in E43 because of it

1. A power statement in the protocol, and a minimum detectable effect computed in the report from the observed spread.
2. Two fixed looks (8 pairs, then 16) at a per-look level of 0.0294 (Pocock; quoted, not re-derived), with a futility stop.
3. Exact Hodges-Lehmann interval, A12 and the exact permutation p instead of a bootstrap of a median.
4. Missing deepest-stage results censored at the reference 1.0 (complete cases as a sensitivity row); times to a target censored at the budget.
5. The seed-1 reproduction check compares proposals, not trajectories.
6. A checklist for the report of a series: protocol tag and deviations; epoch, arm hashes, chain heads; n per cell with missing and censored runs; effect with interval, A12, exact p and the minimum detectable effect; the full per-seed curves; workload parity; the script by hash and the run table; failed runs kept; what the series cannot show.

## Sealing and publishing (for the pilot bundle)

BagIt-style layout (ledger copies made with SQLite's backup API, anchors, each ledger's head and chain verification, the arm definitions, the epoch inputs, the commit and lock file, Python/numpy/scipy versions, CPU model and BLAS thread settings, the analysis scripts, a payload manifest and a tag manifest, a detached signature over the tag manifest, the chain heads stored off the machine). A public derived view is built from a column **allowlist** (arm, seed, stage, spec hash and operators, metrics, `cpu_s`, timestamps, epoch); event payloads, hint text, agent tool outputs, paths, hostnames and environment values are never published; the full bundle stays private. A redacted view cannot be verified against the chain unless per-event hashes are published, and whether that is safe was not checked.
