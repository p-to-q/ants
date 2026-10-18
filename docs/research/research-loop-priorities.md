# Priority audit: an evidence-producing research loop, not a feature collection

Decision, 2026-10-19. Scope: review the newly added planning items against the
existing roadmap and implementation. This is a repository/logic audit, not a
new literature review, performance result or change to E43a.

## Goal and actual boundary

The useful product is a bounded loop: propose a testable candidate or question,
choose the next allowed evaluation from current evidence, execute it under the
contract, retain the result and failures, then update the next choice. Judge
quality by **quality at a fixed budget, time to a stated target, success over
independent seeds, and evidence that survives verification** (roadmap 31).
The search graph and future outcomes are unknown. Neither a good model nor an
LLM recommendation establishes the globally optimal path. Use "best found under
the declared budget/space"; compare policies prospectively before calling one
more efficient. Research value can be a well-supported negative result.

Today, the strongest automated loop is typed model-configuration search within
one declared gym. A general agent that invents and independently verifies whole
scientific programmes is not implemented. Keep that distinction visible.

## What was misordered, and the correction

| Addition | Assessment | Correction and original dependency |
|---|---|---|
| Startup, health-only review, recovery and shared UI tokens | useful operating support, not evidence of search quality | finish small fixes; stop polishing the interface once it is usable; do not change active trials (28) |
| Model exporter | necessary for model hand-off, too broad if made the next research objective | first a reproducible result manifest; then one small frozen-finalist export with prediction parity, not a checkpoint service or export of every trial (24/26) |
| Warm starts | directly relevant to making use of past work | split configuration-bank reuse from weight transfer; the first does not require model serialization; distinguish cold-vs-warm from LLM-vs-no-LLM given the same bank (19/41) |
| LLM proposal support | a reasonable experiment, not the definition of automation | restore fair simple baselines, allocation/yield logs and bounded cost before spending on another full comparison (2-4, 16-17, 35-39) |
| Jev | optional uncertainty/utility predictor, not the trusted scientific judge | compare with the existing local screen and a simple rule, first in isolated shadow; calibration alone does not demonstrate better search (22/35/36) |
| Persistent PI, shared memory, multi-agent/meta-controller | potentially useful, currently a large speculative branch | one cheaper predecessor must earn its complexity before the next is built; retain only bounded, independently checkable hypotheses (30-33/41) |

The previous new table made delivery features and external services look like
the next compulsory research sequence. That displaced the original strongest
idea: establish what the existing portfolio gains over simple search, identify
the mechanism, and only then add complexity. E43a tests one screening mechanism;
it does not replace the reference and ablation experiments.

## Minimal evidence gate before another learned controller

1. Keep the full run/spec/epoch/policy/data identity, candidate lineage, failures
   and trial cost. Review `operator_alloc` and `screen_decision` before inventing
   new telemetry. Existing rounded operator shares are not exact probabilities
   of selecting a complete configuration; do not pretend they identify arbitrary
   off-policy estimates (35/36). Old outcomes for untried actions do not exist.
   Exact propensities and a general off-policy estimator are not prerequisites
   for a sound prospective A/B test; do not build that infrastructure just to
   compare a simple baseline. They are required for claims that rely on it.
2. Implement the same evaluation harness for random and one properly configured
   classical reference; compare paired seeds at B, with random also at 2B. A
   weak default TPE wrapper is not a strong reference (16). Expand references or
   ablations only where the hypothesis justifies their cost (17).
   Declare a useful effect/cost threshold and a plausible seed/budget plan before
   committing the next long run; do not copy E43's sample size into every question.
3. Report the protocol's charged trial CPU, whole-service CPU/overhead, wall time,
   and API calls/tokens/money separately. Do not silently combine these into a
   favorable scalar or change what the current CPU budget means. A new policy
   must state its resource limits and success/stop rule before a forward run.
4. Preserve fixed roles and point-in-time evidence. If candidate selection,
   target selection, model fitting and calibration all see future outcomes,
   the apparent improvement is not a prospective result. Existing limited
   development families restrict a cross-domain claim (37); no general-autonomy
   claim follows from a single forecasting gym.

As inspected, `History._ingest_first_stage` credits feasible improvement/opening of an
archive cell and the portfolio mixes that with lineage credit. Historical prose
calling this simply "promotion rate" is not a precise account of current code.
It is also not verified improvement of the deepest-stage frontier per total
cost. Measuring that gap is a future hypothesis, not permission to change the
active reward, screen, reserve or arm definition.

## Jev is not the evaluator

`judge.py` is a client/calibration/triage library, not a running service in the
campaign. Its current label is first-stage top-20%, not a proof of deep-stage
gain. Its numeric shadow/advisory thresholds are implementation defaults, not
an approved scientific promotion rule; `ProposalTriage` can reorder, not filter.
The advisory transition changes the algorithm and requires a declared policy,
hold-out calibration and prospective search-utility check including API cost.
Inputs must be available at prediction time; outcomes can resolve later against
a predeclared target/cohort, not a threshold retuned to favor the predictions.
Do not switch authorities automatically during a fixed comparison or assume
mock endpoint tests establish a real available service. A negative calibration
or no gain over the local predictor closes/defers the branch.

## Reuse without losing experimental meaning

A frozen configuration bank can be passed to a new search after explicit role,
split, preprocessing and space compatibility checks. Carry provenance as a
prior; re-evaluate under the receiving identity and never relabel old scores as
new ones. A missing checkpoint may be deterministically refitted using the
frozen training recipe, but that artifact is marked regenerated. A weight prior
also carries all stored parameters, including the frozen portion, into the cap.

If asking whether warm starts help, bank access is the treatment. If asking
whether an LLM helps after warm start, the bank and information access are held
constant. Report both the conditional return on new compute and an amortized
view including bank construction; do not present sunk compute as a free
end-to-end efficiency gain. A large factorial design is not required by default:
start with one independently interpretable contrast.

## Work now versus integrate later

Now: write protocols and minimum manifests; develop the small health-only and
finalist-export tools on toy or old immutable pilot data; prepare reference
harnesses and inspect existing decision logging. No extra active server workers,
Jev calls, LLM proposals or interim tuning are authorized by this planning change.

E43a uses development data. A positive result is evidence about that registered
search comparison, not by itself a held-out generalization or publication claim.
External generalization claims additionally need the freeze/confirm/sealed
workflow (27), independent data and explicit look accounting. Operating-system
isolation (25) remains a prerequisite for executing model-written code, not an
optional consequence of a good LLM proposal score.

After the E43a protocol releases the run slots: read its result using the frozen
analysis, including validity checks and any registered continuation. Choose one
next experiment from its evidence and the reference gaps. Do not automatically
schedule warm starts, LLMs, Jev and multi-agent work one after another just
because they appear in a roadmap. New representation, data roles, evaluators or
sealed looks still require their own human-approved protocol and identity.

Acceptance of this planning change is consistency with those boundaries and
links to independently testable work, not completion of the planned features.
