# E2 Confirmation - is the improvement real and general?

**Emits:** a verdict, never a score. Cf. EvoCast's accepted / rejected / unstable / failed.

Challenger vs incumbent on IDENTICAL windows (`registry.decide_promotion`, entered through `Registry.challenge`):
* pooled paired moving-block bootstrap, equal weight per source family: `P(improve) >= 0.95` and relative gain >= `delta` (practical threshold, default 0.5%);
* at least `min_families` (default 2) independent families individually improve - the requirement is never relaxed to "all families present"; no family is significantly worse;
* MAE must not worsen (a gain only under MSE is not accepted); a challenge without MAE losses is `failed`;
* **accepted** -> `validated`; **unstable** (positive but unsupported, e.g. single source, or not significant at the online-FDR level) and **rejected** are terminal; **failed** changes nothing.

## What counts as comparable evidence (fail closed)
A loss record (`Registry.loss_record`) carries, per family, the per-window losses **and the hash of the ordered window identities** they were measured on (`metrics.window_hash`; duplicates refused). `challenge` returns `failed`, changes no status and spends no alpha unless:
the candidate record is for the challenged model; the incumbent record is for **the incumbent of the cap bin** (the champion, else the most recently validated model; a challenger cannot pick a weaker incumbent); both come from the same epoch; both cover **exactly the same families** (a challenger cannot drop the families it loses on); every family's window-id hash is identical; every family has at least `2 x block` windows; all losses are finite and non-negative; the incumbent's mean loss is positive.

## The ladder only moves with proof
| step | entry point | proof it records |
|---|---|---|
| candidate -> confirmed | `Registry.confirm` | `model_gates` event: finite metrics, within the cap bin, beats the reference **by a margin** (the seasonal-naive spec itself scores 0.9999999995), beats every supplied trivial forecaster (last value, mean), replay reproduces the metric |
| confirmed -> validated | `Registry.challenge` | accepted by `decide_promotion` AND pooled p-value <= the LORD++ level of this test; row in `alpha_wealth` + hash-chained `alpha_wealth` event |
| validated -> champion | `Registry.transition(..., {'freeze_hash': h})` | `h` froze exactly this model's spec and has a successful `sealed_result` (see E7) |
A bare `transition()` to any of these raises `PromotionError`. `Registry.verify()` replays the model events and reports any row of `models` that was edited behind the registry's back. The first incumbent of a bin is a deliberate, logged human decision: `Registry.register_baseline`.

## Multiple testing: online FDR (LORD++)
Every challenge is one hypothesis in an unbounded stream; "accept at 95%" applied to thousands of challenges promotes noise. Each challenge `t` is given the LORD++ level `alpha_t` (alpha = 0.05, wealth w0 = alpha/2, pinned by the first recorded test so a later `Registry(..., fdr_alpha=0.5)` cannot loosen it) computed from the promotions so far; a model is promoted only if the other rules accept **and** its pooled one-sided p-value is `<= alpha_t`. Rules that fail offer p = 1 to the procedure, which is valid. The p-value is the larger of a block-bootstrap-SE normal p-value and a batch-means Student-t p-value whose batch length is at least 3 x the autocorrelation time of the loss differential (overlapping windows). Consequences to plan for: the first test has a level of ~1.3e-3 and the level decays like a spending sequence while no one is promoted, so a long streak of failed challenges makes a marginal gain hard to promote (a clear gain still passes); every promotion tops the level up. Tested: 20 streams x 100 null challenges, plain rule promotes ~1%, LORD++ promotes none. LORD++ assumes independent (or positively dependent) p-values: challenges that reuse the same incumbent windows are positively dependent, which is the supported case.

Rationale: recent LTSF benchmark studies show that small protocol or metric changes overturn claimed state of the art, so a
point estimate on a single dataset is never enough. `metrics.diebold_mariano` (Newey-West, HLN) and
`metrics.indistinguishable_from_best` are available for end-of-campaign reporting.

## Negative controls and "no skill"
`rel_mse < 1` only says "better than seasonal naive": on white noise the *mean* forecaster scores ~0.5 and on a random walk the *last value* ~0.8. Use `metrics.beats_trivial(model_mse, X, Y, period)` (best of last value / window mean / zero / seasonal naive) as the skill test; on a negative-control dataset the expected answer is False and a True is a pipeline leak.
