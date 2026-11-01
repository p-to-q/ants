# Saved models in Leaderboard

Decision: next version. This specification describes planned UI and harness work; it is not installed on the E43 server.

## Purpose and placement

A researcher needs to answer: which model should I inspect, why was it retained, and can I actually load and verify it? A visitor should be able to see what the search found without reading an experiment protocol. Leaderboard therefore gets two modes, **Live** and **Saved**. Past stays a round-level history. No extra top-level page, duplicated page title or introductory essay is needed.

Live keeps the current cap tabs and best/latest order. Saved is global to the configured research collection and does not silently change when the live arm changes. Its header shows the collection (for example E43), epoch and cache freshness. Different epochs and evaluation contracts are separate collections; they never share a numeric rank.

At 120 columns or wider, Saved uses a model list (roughly 60%) and selected-model details (40%). On smaller terminals, details sit below the list; Enter opens the full record. Use the existing terminal palette, typography, selection, key conventions and spinner. The useful visual signature is a compact lineage trail in the detail panel, not another large chart.

```
Live   Saved                        E43 / epoch …       updated …
cap: all 100 300 1000 3000

model     dev error  params  kept for      | period · pool prior
…         …          …       cap best      | E43b / seed 11 / trial …
…         …          …       different core| Previous best → current
                                           | Spec saved · refit verified
                                           | Local backup verified on …
                                           | Enter: evidence and files
```

Rows show a short stable model id, development score, nominal parameter count, one retention reason and one availability indicator. The panel shows core/pretraining/decomposition, source run/trial, a small number of active differences from its predecessor, original evaluation scope, and available weight variants. Full specs, fold/horizon inventories, hashes, lineage and paths belong behind Enter. No raw hash chains or absolute paths in the default list.

Every score is attached to a specific evaluation. An original trial score is labelled **Development selection**. A new refit's score is blank until that exact bundle is evaluated; its historical trial score is never copied into that slot. Physical stored parameters and nominal evaluator parameters have distinct labels. Training or parity success does not become a quality result.

## Selection and long-term preservation

Ten is the active research shortlist, not a lifetime deletion limit. Retain all former selections and version records; a new shortlist supersedes an old one. Default Saved shows the current ten; a Previous filter reveals earlier shortlists and previous-best transitions. Selection labels are reasons, not ranks of scientific merit.

Start by keeping the best available specification at each of four nominal caps. Deduplicate by active canonical spec. Add technically different candidates with good development performance: core, ensemble, decomposition, normalization, prior, lookback and robustness settings. An initial 3% within-cap score band can limit the diversity pool, but is a disclosed curation heuristic, not an equivalence test. Include a weaker route representative only with an explicit comparison/reference label. Never fill a quota by claiming a weak candidate is a winner. If evidence supports fewer than ten, show fewer.

The November 2 provisional shortlist is limited to 128 per-run/cap winners (112 distinct active specs), not all 13,937 S2 trials. Of those distinct specs, 111 are period and one is Fourier. It can seed a research collection but cannot establish broad architecture diversity. The next selector should read all eligible deepest-stage records and retain best representatives by technical route as well as cap. Seed repetition is not evidence of stability of a particular spec unless the same frozen spec is evaluated across seeds.

No unvalidated composite novelty score or LLM judgement determines preservation. A deterministic selector writes its policy version, source set, reasons, exclusions and tie-breaks. A researcher may pin a candidate with a written reason. Pinning cannot edit its score or evaluation status.

## Records, operations and failure states

Use a versioned collection manifest linking immutable candidate records and separately immutable weight bundles. Record collection id/epoch/contract, source ledger identity and chain anchor, spec id, source trial/run/seed, original metrics, retention reason, predecessor, bundle ids and their dataset/horizon/fold, actual storage count and role, source/environment/data recipe identities, and backup receipts. Store scores on evaluation records rather than on a mutable model name.

Offer `show`, `compare`, `export` and `pin` operations through the same CLI/API used by the TUI. Show/compare are read-only. Export copies and verifies an existing bundle; it does not start refitting. A refit is a separately named explicit or scheduled harness task. Keep terminal key bindings to one new mode switch plus existing arrows/Enter; no hidden training action on opening Saved.

Use separate availability fields:

- Spec retained; historical weights unavailable.
- Refit pending / failed / verified. Verified means bytes and inference parity, not scientific validation.
- Original checkpoint retained, with its precise fold/horizon scope.
- Local copy verified at a timestamp. Offsite receipt is absent until such a destination exists.
- Confirm evaluation not run / completed, with a concrete receipt. A publication-ready badge needs an explicit release review, never a score threshold.

For missing bundles, keep the model row and say what is missing. For a bad checksum, refuse loading and show the last verified receipt without calling it current. For stale cache, keep the last readable list with a timestamp and stale label. For malformed catalog, isolate the invalid record when identity boundaries can be validated; otherwise fail the collection closed with repair guidance. Paths and errors use existing text sanitization. Model inspection must not depend on a running worker or the original server still existing.

## Harness automation

Existing candidate watch automatically detects improved per-cap specifications. Existing refit/export can produce complete numeric model bundles and verify roundtrip predictions. Neither is installed as an automatic server weight-retention pipeline. The provisional shortlist is packaged by a batch script using those same functions; no hand-written weight extraction is required.

For the next version, provide one resumable retention job: scan eligible dev records → deterministic selection → persist versioned manifest → enqueue missing allowed refits/checkpoints → verify → update availability → back up → publish a cached read model. Content ids make retries idempotent. One training/export worker, a bounded queue and disk thresholds fit the small server. Limit initial refits to declared dataset/horizon variants; do not schedule every variant of every historical trial. Retention failures are visible and never stop evidence recording or delete the last verified version.

Recovering original historical weights is impossible when they were never written. To preserve future exact fitted states, instrument the evaluator before process exit, with dataset/horizon/fold/training-window/seed identities. Stage potential winners, let the controller verify them and publish an immutable receipt, and pin retained weights against janitor cleanup. Archive and verify before removing unselected raw intermediates. This changes runtime evaluation plumbing and needs a fresh code/epoch identity and acceptance before another scientific series.

## Delivery order and research gates

1. Accept immutable collections and a CLI export/read API, with atomic updates, corrupt/missing file handling and recovery checks. Preserve the current E43 shortlist locally now.
2. Add the Saved view against the cache, wide/narrow layouts, Previous transitions and available-file details. Demonstrate empty, stale, failed export and missing-weight cases alongside the populated view.
3. Add the single-worker retention queue and evaluation-time checkpoint hook. Test interrupted writes, resumed tasks, full disk and restore on a separate process. Resolve nominal versus actual-storage cap accounting before claiming a deployable cap.
4. Before a new model-quality claim, freeze candidate specs and evaluate a small, predefined comparison on appropriate roles. For research progress, establish missing random B/2B and tuned Optuna references first. The cap-3,000 screening effect can motivate a separately registered follow-up. Do not simultaneously change selection, sampler, warm start and LLM advice.

README should expose a small development frontier and a link to a dated collection of retained models. Update those only from verified frozen manifests, with source identities and scope. Keep formal benchmark placeholders until their own protocol is run. Screenshots should match the released UI; no mock Saved screenshot presented as an installed feature.
