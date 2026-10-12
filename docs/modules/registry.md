# registry

Model registry and promotion verdicts. Library code: nothing in `ants run`, the CLI or any deployed unit creates a `Registry`; only tests drive it. `tinyts` does not emit per-window losses or window ids, so there is no producer for `loss_record` yet.

**Interface**
- `decide_promotion(cand, inc, delta, p_min, min_families, block, mae_ratio, min_windows)` -> verdict `accepted | unstable | rejected | failed`.
- `Registry(ledger, fdr_alpha=0.05)`: `add`, `register_baseline` (first incumbent of a bin, a logged human decision), `confirm` (candidate -> confirmed), `loss_record` + `challenge` (confirmed -> validated), `transition(mid, to, evidence)`, `champion`, `incumbent`, `table`, `score(s)`, `verify`. Errors: `PromotionError`.
- Tables: `models`, `alpha_wealth`, `model_scores` (created in the ledger database).

**Invariants**
- Ladder candidate -> confirmed -> validated -> champion (then retired); terminal `rejected`, `unstable`, `retired`. One champion per (gym, cap bin); every transition is a hash-chained `model_status` event.
- Forward steps need proof recorded by their entry point; a bare `transition()` to `confirmed`, `validated` or `champion` without it raises `PromotionError`, an illegal move raises `ValueError`:
  - `confirm`: finite metrics, within the cap bin, beats the reference AND every supplied trivial forecaster by a relative `margin` (default 1e-3), replay reproduces the metric; recorded as a `model_gates` event.
  - `challenge`: returns `failed` (no state change, no alpha spent) unless the candidate record belongs to the model, the incumbent record to `incumbent()` (champion, else most recently validated), both are from the same epoch, the family sets are identical, the window-id hash is identical per family and MAE losses are present; then `decide_promotion`, then the pooled p-value must be <= the LORD++ level of this test (`metrics.lord_level`; `alpha_wealth` row + event; alpha and w0 pinned by the first recorded test).
  - champion: `evidence={'freeze_hash': h}` where `h` froze exactly this model's spec and has a successful recorded `sealed_result`.
- `decide_promotion` fails closed: identical family sets, at least `2 x block` windows per family, finite non-negative losses, positive incumbent mean; at least `min_families` families improve (default 2, so a single family cannot be `accepted`; the requirement is never relaxed to the number of families present), none significantly worse; MAE must not worsen.
- `verify()` replays `model_registered` / `model_status` events and reports any `models` row edited behind the registry's back.

**Notes / failure behaviour**
- The registry trusts the loss vectors and window ids it is handed; nothing proves they were measured by the evaluator. The first incumbent, `fdr_alpha`, `unseal_limit` and `unseal_lifetime` are human numbers (limits only ratchet down inside one ledger; a fresh ledger resets them).
- LORD++ starts at a level near 1.3e-3 and decays while nothing is promoted: a marginal gain after a long streak of failed challenges is refused. It assumes independent or positively dependent p-values.
- With one `unseal_limit` and several cap bins, a champion per bin needs several sealed looks; that is a multiplicity the claim must correct for (E7).
