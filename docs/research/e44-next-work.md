# Next work after E44a — November 3, 2026

Status: development plan, not a registered runtime protocol. Preserve the finished E43 and E44a identities. The system's objective is a reproducible small model and a measured reason to use its search procedure, under an explicit resource envelope.

## Priority 1: usable model evidence

Finish [E44a acceptance](../experiments/E44a-acceptance-20261103.md): retain the verified raw-artifact receipt (1,028 files checked); keep original checkpoint and new refit statuses separate; preserve rejected exports. Add a bounded evaluation-time checkpoint hook and dataset/horizon/fold receipts before another rollout advertised as producing publishable trained models. Opening a viewer must never train a model or update its evidence. The current successful etth1/h96 refits do not recover historical weights.

## Priority 2: one Optuna reference shared by two entry paths

Build a new adapter and new arm identity; do not silently alter `tpe` or the existing portfolio. A standalone reference and an optional portfolio operator must call the same implementation. The acceptance order is:

1. Specify an explicit, versioned conditional mapping from the existing canonical model specification to active Optuna suggestions. Keep inactive defaults for evaluator compatibility but omit them from the sampler observation. Prove identical effective configurations receive identical canonical keys. Do not invent a generic conditional map from parameter names.
2. Keep one fidelity per objective study and scope all observations by gym, arm, epoch, contract and space identity. Handle finite values, duplicate proposals, failures and infeasible candidates explicitly. Preserve every charged evaluation.
3. Use ask/tell with a durable correspondence between proposal ids, Optuna trials and ledger attempts. Reconcile stale/pending attempts on restart from authoritative fencing/lease outcomes. A sidecar study is rebuildable state; the ledger is authoritative. No second writer to scientific records.
4. Pin Optuna version and every effective sampler setting, including startup count, multivariate/group policy and constant-liar policy. Test conditional suggestions and batching using the pinned API rather than relying on version-dependent defaults.
5. Establish repeatable proposal streams for the declared restart boundary and record limitations under asynchronous completion. Test crash-before-enqueue, crash-after-enqueue, retries, duplicate tell and exhausted budgets.
6. Measure controller wall/CPU/RSS and proposal latency independently. Report evaluation CPU and total cost; require the two-worker reference to stay inside the target memory envelope before running a long comparison.

The current adapter rebuilds a flat study from first-stage observations and does not satisfy this contract. No new adapter is claimed shipped here. Official API basis: [Optuna ask/tell](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/009_ask_and_tell.html). Constant liar penalizes pending trials; it is not proof that an abandoned trial is still running.

## Priority 3: neural training without changing the installed evaluator

`scripts/prototype_tiny_mlp.py` is a standalone NumPy 16–8–1 tanh network: 145 trainable values, 177 stored numeric values including input preprocessing. A fixed 600-step training recipe uses synthetic data only, saves the last iterate, and checks exact prediction parity after reload. A finite-difference test checks backpropagation. A train-only ridge reference is a smoke diagnostic, not a compute-matched benchmark. Source, weights, seeds, training-data hash and a manifest are exported together.

This is not a time-series gym, a search arm, a real-data result or evidence that neural training beats closed-form fitting. Next implement a separate gym contract with chronological splits, fixed windows/horizons, deterministic seeds, capped steps/wall/memory, train-only preprocessing, exact checkpoints and a same-cap closed-form comparator. Keep model parameters, preprocessing values, optimizer-state cost and transient training memory distinct. No large framework or GPU dependency is justified by this first prototype.

## E44b admission and design

The next scientific factor is proposal strategy: unchanged portfolio versus the audited standalone reference versus unchanged random at B, plus random at 2B as the cheaper-method gate. Promotion, data, initialization and evaluator remain fixed. Do not combine LLM, Jev, screening, warm start, two-choice allocation and a new neural evaluator in this contrast.

Registration still needs exact adapter commit/settings; a primary cap and fixed endpoint; B chosen from completion/overhead evidence; independent paired seeds; sample size justified by a prespecified practically useful effect and uncertainty; stopping/missing-run rules; and finite confirm-look accounting. Do not choose these from whichever E44a cap looks best. Use development data for search comparison; reserve an independently frozen finalist and held-out protocol for model-performance claims. No E44b TOML is marked running and no workers are launched until these fields are fixed.

Do not fill an idle server with an unregistered multi-day search just to display activity. Offline development and bounded synthetic checks can proceed while these gates are completed. A separate CPU-bounded soak may test infrastructure, but must be labelled engineering and cannot later become efficacy evidence.
