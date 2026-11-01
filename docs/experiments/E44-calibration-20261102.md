# E44: preserved-model and reference calibration

Decision recorded November 2, after E43's final look. This is a new study family, not E43c or another look at E43. No seeds are appended to the closed E43 protocol.

## E44a: bounded engineering calibration

The question is whether the new release can run all reference paths, preserve feasible candidate evidence, produce loadable weight variants and report their actual development validation consistently. It is **not** an efficacy or benchmark-superiority test.

- Arms: existing `random`, existing Optuna-backed `tpe`, and `portfolio`. The existing tpe arm is a plain first-stage TPE reference; it is not advertised as the strongest tuned Optuna configuration.
- Seeds: 101 and 102 for each arm, six independent workdirs. Separate `/var/lib/ants/series/e44a` base.
- Budget: 3,600 charged CPU-seconds per run, two workers. Retain the same TinyTS evaluator/contract epoch `5da6eeee6527`, role boundaries and caps. Record the new 0.6.3 source and exact arm definitions.
- Resource envelope: single-threaded BLAS, existing two-worker 180% CPU/1,500 MiB memory limit. Approximately 6 CPU-hours, roughly 3–5 wall hours conditional on actual capacity.
- No LLM calls, warm starts, screen adoption, policy migration, new data or evaluator mutations.

Acceptance checks all six budgets/identities/chains, distinct source cohorts, no unseals, failure/timeout frequencies, preservation receipts, physical-versus-nominal cap gaps, model reload/parity and per-dataset/horizon/fold coverage. Export/check at least the feasible retained variants; an over-cap export remains a recorded rejection. A refit is labelled a new fit. Preserve all failures and older model versions.

An explicit completion task may derive caches and new development-only weight validation reports. Reports are descriptive; they do not choose or alter any running arm. Source protocols and evaluation records remain immutable. E43 map and saved-model collections remain separately accessible.

The ten ETTh1/h96 saved-weight checks already performed on November 2 were unregistered engineering/display acceptance and are not retroactively included as a preregistered E44a endpoint. Their original search scores, saved-weight results and data roles remain separate.

## E44b: the next scientific comparison, not yet registered

Before allocating another multi-day search campaign, freeze the changed factor and establish a defensible reference. The primary new factor should be **the proposal/search strategy**; keep evaluator, promotion, initialization, data, caps and charged budget fixed. Compare portfolio with properly configured maintained Optuna and random at B, including random at 2B as the cheaper-method gate. The current plain tpe arm alone does not settle that gate.

Resolve physical storage counting before making deployable parameter-cap claims. Freeze exported finalist weights, preprocessing, training recipe and exact validation convention, then test confirm roles only through the existing freeze/seal gate with a declared finite look budget. Do not retune after looking. Use per-dataset/horizon standard metrics and uncertainty compatible with dependent time-series windows; pooled dev minima do not qualify as a published benchmark result.

The E43 cap-3,000 screen signal can motivate a separately registered follow-up with independent seeds and a declared primary cap. It does not justify adopting screening in E44a or calling additional seeds E43c. LLM advice, warm starts, migration and evaluator changes each need separate contrasts. Write E44b's sample size, stopping rule, endpoint, comparator settings and analysis before execution; this document does not claim those unspecified choices are registered.

## Variables to add now

Add **evidence fields**, not new search degrees of freedom: actual stored numeric count and bytes; exact weight/source/recipe identities; preservation status; per-dataset/horizon/fold coverage; validation-window count; runtime and failure outcomes; and artifact retrieval/parity results. These reveal whether an apparent improvement is real and usable.

A later changed search-space lever or neural architecture starts a new evaluator/space identity and requires its own reproducible reference. Research progress means a frozen, reproducible improvement under a clearly stated task/budget, rather than a more elaborate controller or a better-looking development leaderboard.

## Optuna and two-choice selection: implementation audit, November 2

Optuna is already inside `build_portfolio(with_tpe=True)` as `TPEHistProposer`; import failure records `tpe` as unavailable. `EvoProposer` already samples two members of its ranked elite pool and chooses the better parent before mutation/crossover. `PerCapEvoProposer` also uses two-way parent selection. Adding another identically named capability would duplicate existing behaviour.

The current Optuna adapter rebuilds a study from bounded first-stage history and asks with a flat distribution dictionary. It is not a persistent, conditional ask/tell integration with reconstruction of real pending evaluations. The server lock pins Optuna 5.0.0: unlike older releases, its documented TPE defaults enable multivariate sampling and constant liar. Therefore older roadmap wording describing this adapter as necessarily univariate is version-specific and must not be used as a current configuration claim. Default flags alone do not fix our flat conditional representation or pending-work reconstruction.

The development priority is one audited Optuna proposal adapter shared by a standalone reference and an optional portfolio operator, with explicit version/settings, active conditional parameters, epoch-scoped compatible observations, pending-trial lifecycle, deterministic restart behaviour, duplicate/failure handling and measured controller CPU/RSS. Never mix s0/s1/s2 losses as interchangeable objective observations. Benchmark proposal overhead separately and report end-to-end resources alongside charged evaluation CPU.

Two possible interpretations of the requested algorithm remain distinct:

- **Binary tournament:** compare two already evaluated feasible parents at a common fidelity, then mutate the winner. This exists today; comparing two newly evaluated children would spend two evaluations and needs its own budgeted contrast.
- **Power of Two Choices:** sample two queues/servers and choose the less loaded. Its load-balancing guarantees do not establish model-search quality. A portfolio adaptation choosing between operators by uncertain reward per CPU is a new hypothesis, not the original theorem.

After E44a acceptance, first compare a frozen audited standalone Optuna reference with random B/2B and the unchanged portfolio. If that adapter is useful and affordable, test its replacement inside portfolio with the controller/reserves fixed. Only subsequently compare the existing allocator with a two-choice allocator using exactly the same operator pool, evaluation budget and reward definition. Record all allocation propensities, overhead, failures and lineage/root diversity; historical replay is diagnostic and cannot evaluate unobserved counterfactual proposals. These are separate experiments, not extra factors silently added to E44a.

References: [Optuna 5 TPE](https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html), [DEAP tournament selection](https://deap.readthedocs.io/en/master/api/tools.html#deap.tools.selTournament), [Mitzenmacher, The Power of Two Choices](https://www2.eecs.berkeley.edu/Pubs/TechRpts/1996/7979.html).
