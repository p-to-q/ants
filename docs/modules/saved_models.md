# saved_models

Read-only projection of a retained-model catalog. `read_catalog` reads the explicit path or `ANTS_MODEL_CATALOG`, then defaults to the personal `models/catalog.json`. It never follows bundle paths, opens weights/ledgers, refits or writes files.

The current format is the dated `docs/models/e43-shortlist-20261102.json`: schema 1, one evaluation epoch/scope, at most 1,000 unique spec records. Reads reuse the bounded regular-file/symlink-safe reader (2 MiB) and strict JSON parser. Mixed metric, stage, direction, gym, epoch or contract scopes fail closed. Nominal counts and recorded physical counts must respect their caps. A missing file is an empty state; malformed/unreadable metadata is an unavailable state. A recorded checksum or `verified_local` status is never a fresh file verification.

The terminal Leaderboard's `v` key switches Live/Saved. Saved works without a live ledger. At 120 columns it displays a list/detail split; narrower terminals stack the detail below a paged list. Entries are not capped at ten. Arrows/Page keys move through the supplied collection; Enter opens the cached record. Saved keeps its own return selection and never resolves trial ids against a current unrelated ledger. Live cap/order keys do not alter Saved.

The default list shows original development score, nominal parameter count and retention reason (`Cap leader`, `Different route`, `Worth a look`). The detail labels refits as recorded receipts and leaves their quality unevaluated. Complete reasons, specs and file identifiers remain available with Enter. Text passes existing terminal sanitization, and all state is rendered as recorded metadata.

This is a first read-only collection view, not the complete planned retention system. Automatic capture, export/verification controls, Previous collections, comparison operations, previous-best transitions and the unified study/stage catalog are still future work. No service is installed and no bundled catalog is automatically assumed to exist on a server.

Tests: `tests/test_saved_models.py`. Status: experimental.
