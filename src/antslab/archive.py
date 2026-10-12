"""Archive backends + upload-then-verify. 'Verified' means the REMOTE bytes were re-read and hashed."""
from __future__ import annotations

import gzip
import shutil
from pathlib import Path

from .ledger import Ledger, sha256_file


class LocalDirArchive:
    """Directory backend: used in tests and as an offline fallback (e.g. a mounted disk)."""
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def put(self, local: Path, key: str) -> str:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(local, tmp)
        tmp.replace(dst)
        return f"file://{dst}"

    def sha256_of(self, key: str) -> str:
        return sha256_file(self.root / key)


class S3Archive:
    """Tencent COS (S3-compatible). Needs boto3; endpoint like https://cos.ap-singapore.myqcloud.com"""
    def __init__(self, bucket, endpoint, access_key, secret_key, region):
        import boto3                                                    # lazy: optional dependency
        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key, region_name=region)

    def put(self, local: Path, key: str) -> str:
        self.s3.upload_file(str(local), self.bucket, key)
        return f"s3://{self.bucket}/{key}"

    def sha256_of(self, key: str) -> str:
        import hashlib
        h = hashlib.sha256()
        body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"]
        for chunk in iter(lambda: body.read(1 << 20), b""):
            h.update(chunk)
        return h.hexdigest()


MAX_LOG_BYTES = 16 << 20       # a runaway gym can write 256 MB per stream; keep the tail, which is where the failure is


def compress_logs(out_dir: Path, max_bytes: int = MAX_LOG_BYTES) -> list[Path]:
    """gzip stdout/stderr after a trial ends; returns the files that should be registered. A log larger than max_bytes is
    stored as its last max_bytes bytes behind a one-line marker (compressing and archiving 2 x 256 MB per failed trial would
    stall the daemon and fill the disk)."""
    made = []
    for name in ("stdout.log", "stderr.log"):
        p = out_dir / name
        if p.exists():
            gz = p.with_name(name + ".gz")
            with open(p, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
                size = f.seek(0, 2)
                if size > max_bytes:
                    g.write(f"[ants: log truncated, {size - max_bytes} of {size} bytes dropped from the start]\n".encode())
                    f.seek(size - max_bytes)
                else:
                    f.seek(0)
                shutil.copyfileobj(f, g)
            p.unlink()
            made.append(gz)
    return made


def archive_pending(ledger: Ledger, archive, prefix: str = "antslab", limit: int = 200) -> dict:
    """Upload-then-verify every artifact that is not yet verified. Idempotent: the key is content-addressed, so a crash between
    upload and `mark_archived` just repeats a harmless overwrite. One bad artifact never blocks the others: a vanished file is
    recorded once and retired, an upload that raises is counted and skipped, a remote hash mismatch is retried next time."""
    stats = {"uploaded": 0, "mismatch": 0, "missing": 0, "changed": 0, "errors": 0}
    rows = ledger.artifacts("deleted_at IS NULL AND (archived_uri IS NULL OR verified_at IS NULL)",
                            order="(archived_uri IS NOT NULL), id", limit=limit)       # never-tried first, retries after
    for a in rows:
        p = Path(a["path"])
        try:
            if not p.exists():
                ledger.event("artifact_missing", {"path": a["path"], "sha256": a["sha256"]})
                ledger.mark_deleted(a["path"], 0)          # retire it, or it would sit at the head of every later run
                stats["missing"] += 1
                continue
            if sha256_file(p) != a["sha256"]:        # file changed after registration: never archive an unknown version
                ledger.event("artifact_changed", {"path": a["path"]})
                stats["changed"] += 1
                continue
            key = f"{prefix}/{a['sha256'][:2]}/{a['sha256']}/{p.name}"
            uri = archive.put(p, key)
            ok = ledger.mark_archived(a["path"], uri, archive.sha256_of(key))
            stats["uploaded" if ok else "mismatch"] += 1
        except Exception:                            # network, permissions, a directory where a file was: next row
            stats["errors"] += 1
    return stats
