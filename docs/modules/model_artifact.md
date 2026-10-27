# model_artifact

`ants candidates refit ID --store STORE --contract CONTRACT --dataset DEV_NAME
--horizon 96 --out NEW_DIRECTORY` fits one preserved TinyTS specification for
one declared development dataset/horizon. It checks candidate/evaluator/contract
identity, refuses roles before loading data, and uses only training targets.
Train-derived standardisation, effective lookbacks, capped training windows,
sampling seed, priors and the target catalog protocol are recorded. Confirm,
sealed and development-test targets are not evaluated.

The output is **refitted_train_only**, not the historical trial's weights or an
E43 endpoint. No automatic retraining follows a new pointer; this command costs
compute and must be explicitly invoked. The four supported horizons are
96/192/336/720. Pool priors, if requested by the spec, use other dev training
families through the existing gym helper. A dataset-specific refit does not
establish transfer or a shared cross-dataset model.

Bundles contain `weights.npz` (all model/trend matrices and mean/scale), a JSON
manifest, provenance/recipe, source snapshot and loading instructions. NumPy
and Python versions and file hashes are recorded. Reloading uses numeric arrays
with `allow_pickle=False`, not executable object serialization. Compressed
weights are bounded on input. `model_artifact.load(PATH).predict(raw_windows,
channels)` accepts rows of raw lookback data and explicit channel identities
when necessary, returning forecasts in raw units.

Before publishing the directory, compare original and reloaded predictions on
training-input probes with absolute tolerance 1e-10. Every dense numeric weight
and preprocessing scalar counts toward the cap; do not treat simulated
pruning/quantisation as an actual sparse/int8 file. Refuse an over-cap artifact
or failed parity. Existing destinations are never intentionally overwritten.
Artifacts remain `release_ready=false`: independent confirmation, claim gates,
dependency/environment reproduction and publication review remain separate.

Tests: `test_model_artifact.py`: four cores including trend heads, cap accounting,
byte corruption, channel handling, role refusal and an actual synthetic-data
train-only refit with future-target sentinels. Status: experimental.
