"""Download the public datasets listed in data/sources.toml into <data>/raw/<id>/<file>. Standard library only.

Rules: https only; resumable (files that exist are kept); every file is hashed while it streams and compared with the
pinned manifest (data/MANIFEST.sha256) when it lists the file; a file whose hash does not match is set aside as
`<file>.rejected` and never used. The manifest is an input here, never rewritten: upstream drift must be an error, not
the new truth.
"""
from __future__ import annotations

import hashlib
import shutil
import ssl
import time
import tomllib
import urllib.request
from pathlib import Path

TIERS = {"dev": ("dev",), "standard": ("dev", "standard"), "extended": ("dev", "standard", "extended"),
         "all": ("dev", "standard", "extended", "chronos")}
USER_AGENT = "ants-lab (+https://github.com/p-to-q/ants)"
MAX_BYTES = 2 << 30
MIN_FREE = {"dev": 1 << 30, "standard": 2 << 30, "extended": 4 << 30, "all": 6 << 30}


CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def tls_context() -> ssl.SSLContext:
    """Certificate checking stays on. Some Python builds (python.org on macOS before `Install Certificates`) ship no CA bundle;
    then use certifi if it is installed, else the system's bundle. Never an unverified context."""
    vp = ssl.get_default_verify_paths()
    if (vp.cafile and Path(vp.cafile).exists()) or (vp.capath and Path(vp.capath).is_dir() and any(Path(vp.capath).iterdir())):
        return ssl.create_default_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for f in CA_FILES:
        if Path(f).exists():
            return ssl.create_default_context(cafile=f)
    return ssl.create_default_context()


def default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout, context=tls_context())


def load_sources(base: Path) -> list[dict]:
    with open(Path(base) / "sources.toml", "rb") as f:
        return tomllib.load(f)["source"]


def pinned(base: Path) -> dict[str, str]:
    """relative path (raw/<id>/<file>) -> sha256, from the pinned manifest; empty if there is none."""
    mf = Path(base) / "MANIFEST.sha256"
    out: dict[str, str] = {}
    if mf.exists():
        for line in mf.read_text().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                out[parts[1].strip().lstrip("*")] = parts[0]
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, opener, timeout: float) -> str:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https source: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    part = dest.with_name(dest.name + ".part")
    h, n = hashlib.sha256(), 0
    with opener(req, timeout=timeout) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            n += len(chunk)
            if n > MAX_BYTES:
                raise ValueError(f"{url} is larger than {MAX_BYTES >> 30} GB: aborting")
            h.update(chunk)
            f.write(chunk)
    if n == 0:
        part.unlink(missing_ok=True)
        raise ValueError("empty response")
    part.replace(dest)
    return h.hexdigest()


def fetch(base: Path, tier: str = "dev", log=print, opener=default_opener, retries: int = 3,
          timeout: float = 120, sleep=time.sleep) -> dict:
    """Fetch every source of `tier`. Returns {"fetched": [...], "have": [...], "failed": [(rel, why)], "mismatch": [...], "unpinned": [...]}."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}: choose one of {', '.join(TIERS)}")
    base = Path(base)
    free = shutil.disk_usage(base if base.exists() else base.parent).free
    if free < MIN_FREE[tier]:
        raise OSError(f"only {free >> 20} MB free in {base}; tier {tier!r} needs about {MIN_FREE[tier] >> 20} MB")
    pins = pinned(base)
    rep = {"fetched": [], "have": [], "failed": [], "mismatch": [], "unpinned": []}
    for s in (s for s in load_sources(base) if s["tier"] in TIERS[tier]):
        rel = f"raw/{s['id']}/{s['file']}"
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = None
        if dest.exists() and dest.stat().st_size > 0:
            digest, status = (sha256_file(dest) if rel in pins else None), "have"
        else:
            last = ""
            for attempt in range(retries):
                try:
                    log(f"fetch {rel}" + (f" (attempt {attempt + 1})" if attempt else ""))
                    digest, status = _download(s["url"], dest, opener, timeout), "fetched"
                    break
                except Exception as e:                             # network, http, size: retry, then report
                    last = f"{type(e).__name__}: {str(e)[:120]}"
                    if attempt + 1 < retries:
                        sleep(2 ** attempt)
            else:
                rep["failed"].append((rel, last))
                log(f"FAILED {rel}: {last}")
                continue
        if rel in pins:
            if digest != pins[rel]:
                dest.replace(dest.with_name(dest.name + ".rejected"))
                rep["mismatch"].append(rel)
                log(f"MISMATCH {rel}: the file differs from the pinned hash; set aside as {dest.name}.rejected")
                continue
        else:
            rep["unpinned"].append(rel)
        rep[status].append(rel)
        if status == "have":
            log(f"have  {rel}")
    return rep
