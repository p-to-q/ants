# Deployment preflight

`ants preflight` reads runtime prerequisites, evaluator/series identities, pinned arm definitions, data manifests, disk headroom, ledger integrity and chains, secret-file permissions, clock and service state. It does not acquire the campaign lock or migrate a ledger. See [operations](../operations.md) for invocation and output.

An arm owns a workdir named exactly `<arm>` or `<arm>_s<integer>`. The seed suffix must contain only decimal digits. In particular, `portfolio_screen_s1` belongs only to `portfolio_screen`, even though it starts with `portfolio_s`. The definition check compares each ledger with its own arm's pin; a real definition mismatch remains a failure.

Tests: `tests/test_preflight.py` covers prefix-sharing arm names, malformed seed suffixes, real mismatches and read-only behavior. A successful preflight is a point-in-time observation, not evidence of multi-day reliability.
