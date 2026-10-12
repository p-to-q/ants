# E3 Artifact - does the thing work as software?

**Emits:** shippable / not. Intended for every model that reaches `validated`. **Not run:** no code decides "shippable"; nothing calls the registry in production.

Implemented now (inside E1 deep metrics, S2 and the final run): finite output under five input corruptions (`metrics.PERTURBATIONS`) summarised as `robustness_index`, quantised and pruned
variants evaluated as the model that would actually ship, exact parameter and byte accounting. Parameters are counted as non-zero weights; the byte figure does not charge index storage for a pruned model.

Not implemented: ONNX export with numerical equivalence on fixed windows (max abs diff), int8 deviation bound,
single-thread CPU latency (median of N), golden-output regression files, retrain-from-spec determinism check. `onnx`, `onnxruntime` and `scikit-learn` are not imported anywhere.
They are meant to run only at promotion time so the 2 GB control tower never imports heavy runtimes in the inner loop.
