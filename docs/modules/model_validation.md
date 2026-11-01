# model_validation

`ants candidates evaluate --bundle PATH --out NEW.json [--max-windows 2048]` checks the exact saved refit weights on their own development validation split. It does not fit or change weights. Bundle checksums and numeric shapes are verified first. Dataset/horizon must match provenance; require dev role before loading data; the training-data bytes must match the saved training checksum.

Validation origins advance by the horizon, targets lie wholly within `[n_train,n_train+n_val)`, and a deterministic channel/window subsample (seed 20261102, max 1–4096) bounds memory. Inputs contain only earlier observations. Standardized MSE/MAE use the saved training scale; relative MSE uses a seasonal-naive forecast on the identical windows. Report available/evaluated windows, selected variant, weight/manifest hashes, per-channel descriptive errors, one raw-unit forecast/actual/baseline trace and a standardized residual histogram. Overlapping-time uncertainty, confirm statistics and publication readiness are not inferred.

This is a new saved-weight validation protocol, not the historical four-family S2 score. Results remain development-only and must not replace the registered E43 endpoint. Writing uses fsync and exclusive atomic publication to a new receipt; existing receipts and the bundle are unchanged.

Saved catalogs may carry a matching validation receipt. Match weight SHA-256, dataset and horizon; reject invalid numeric metrics/trace. The public catalog can omit raw trace arrays while retaining aggregate scores and hashes. A local catalog with trace supports `t` on Saved to compare actual/forecast/seasonal-naive curves in native terminal coordinates. No trace is fabricated when absent; the view explains where the receipt is needed. Opening Saved never triggers this evaluation.

The November 2 engineering check covers ten exact ETTh1/h96 bundles over 210 windows each. It was not preregistered and is used for artifact/display acceptance, not confirmatory research or a model-selection significance claim. All source roles and benchmark gates remain unchanged.

Tests: `tests/test_model_validation.py`, saved-catalog tests. Status: experimental.
