# tiera

`ants tier-a --spec FILE...` or `--trial ID... --workdir DIR` refits TinyTS
specifications and reports per-development-dataset/per-horizon standardised
MSE and MAE, seasonal-naive reference errors and relative errors. It reuses the
gym's model fit path and only accepts validation splits and development roles.
It neither opens sealed/test windows nor writes to search ledgers. Trial
lookups are read-only and missing files are never initialized as ledgers.

`--lookback` is an explicit positive override. Counts follow the gym's reference
horizon 720, not a claim that weights were exported. `--csv FILE`, `--json` and
`--out FILE` support reporting; a requested CSV file is actually written.

This is a development validation tool, not the complete Tier A benchmark:
lookbacks/folds follow this evaluator, MASE and bootstrap intervals are absent,
and results are labelled non-comparable with published tables. It refits rather
than loading saved trial weights. Uncapped train/validation windows can be
expensive: run explicitly on suitable local compute, not automatically on the
two-worker E43 server. It is not a finalist exporter or promotion decision.

Tests: `test_tiera.py` (real-dev-data checks skip if unavailable),
`test_metrics_display.py`. Status: experimental.
