# Tiny MLP prototype

`scripts/prototype_tiny_mlp.py --out NEW_DIRECTORY` runs an isolated, bounded synthetic training/export smoke. It does not register a gym or touch a scientific ledger. Requires the existing optional NumPy dependency.

The fixed 16–8–1 tanh architecture has 145 trainable parameters; input mean and scale bring the saved numeric count to 177, checked against 300. Training uses only the first 512 synthetic samples and 600 fixed steps. The remaining 256 samples are validation diagnostics, never used for early stopping or selection. Ridge is a diagnostic on the same train split, not an equal-compute comparison.

The fresh directory contains numeric weights, preprocessing, exact source and a manifest with identities, training curve, validation diagnostics and reload parity. Existing output directories are refused. No pickle, external dataset, model endpoint or server service is used. `tests/test_tiny_mlp_prototype.py` checks analytic gradients against finite differences, meaningful training progress, the physical count and exact reload predictions. This is experimental engineering evidence, not a benchmark result or production training harness.
