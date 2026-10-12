# janitor

Disk watchdog and safe cleanup.

**Interface**
- `disk_level(free_gb, thresholds)` -> `ok` / `warn` / `clean` / `pause` / `hardstop`; `free_gb(path)`.
- `plan_cleanup(ledger, need_bytes, hot_s=6h, keep_trials=(), now=None)` streams artifact rows oldest-verified first until `need_bytes` of real files are planned. `execute_cleanup(ledger, plan, dry_run, hot_s, keep_trials, now)` -> `{deleted, skipped, missing, freed, dry_run}`.
- `ants janitor [--archive-dir D] [--dry-run] [--force-bytes N]` (see [cli](cli.md)).

**Invariants**
- One rule, `_eligible`, is applied to a FRESH ledger row at plan time and again at delete time: not deleted yet, not protected, archived, `verified_at` set, kind not in `NEVER_DELETE`, remote hash equal to the recorded hash, verified at least `hot_s` ago, its trial neither queued nor running, its trial not in `keep_trials`. Then the file on disk is re-hashed and must equal the recorded hash. A file protected, re-registered (verification reset), re-verified (hot again) or whose trial was requeued after planning is skipped.
- A `verified_at` in the future (clock stepped back) gives a negative age, so the file is hot and is never deleted. A forward clock step shortens `hot_s` (the janitor is stateless and cannot detect it); deletion still needs the verified remote hash.
- A planned file that is already gone is retired with `mark_deleted(path, 0)`, does not count toward `need_bytes` and so cannot fill the plan; a directory or unreadable file is a skip, not an exception.
- `dry_run` evaluates the same checks and changes nothing.

**Notes / failure behaviour**
- `keep_trials` is supplied by the caller. `ants janitor` passes an empty list, so the "keep the best of each cap bin" protection is not applied by the CLI today; the artifacts of the best trials are protected only if they were registered `protected` (metrics and spec files of full-record stages are).
- The age and keep checks are re-evaluated with the `hot_s` / `keep_trials` the caller passes to `execute_cleanup`, not with the values used for planning.
- Deleting is by path, unlink then `mark_deleted`: a crash between the two leaves a row for a missing file, which the next run retires.
