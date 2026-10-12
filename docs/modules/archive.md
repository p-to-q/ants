# archive

Archive backends and upload-then-verify.

**Interface**
- `LocalDirArchive(root)` (atomic `.part` rename), `S3Archive` (S3-compatible, needs `boto3`, never run against a real service), `compress_logs(out_dir, max_bytes=16 MiB)`, `archive_pending(ledger, archive, prefix, limit=200)` -> `{uploaded, mismatch, missing, changed, errors}`.
- Backends need `put(local, key) -> uri` and `sha256_of(key)`.

**Invariants**
- Verified = remote bytes re-read and hashed, and equal to the hash recorded when the artifact was registered (`Ledger.mark_archived`).
- Keys are content-addressed (`prefix/<sha[:2]>/<sha>/<name>`), so a crash between upload and `mark_archived` repeats a harmless overwrite.
- Selection is `archived_uri IS NULL OR verified_at IS NULL`, never-tried rows first: a remote hash mismatch is retried on the next run.
- A file changed after registration is never archived (`artifact_changed` event). A vanished file gets one `artifact_missing` event and is retired (`mark_deleted(path, 0)`).
- One bad artifact never blocks the others: an exception from `put` / `sha256_of` is counted in `errors` and the loop continues.
- Logs longer than 16 MiB are stored as their tail behind a one-line marker.

**Notes / failure behaviour**
- Permanent failures are not remembered: 200 or more rows that keep failing would still fill the `limit=200` window and starve later artifacts, and `artifact_changed` is emitted again on every run for the same path.
- The CLI wires only `LocalDirArchive` (`--archive-dir`); `S3Archive` is not reachable from any command and has no test.
