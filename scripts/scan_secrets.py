#!/usr/bin/env python3
"""Secret scanner for the public package (stdlib only). Used by scripts/pack_public.sh on the staging tree AND on the final tarball.

    scan_secrets.py DIR          scan every file under DIR
    scan_secrets.py --tar FILE   scan the members of a .tar.gz (names, link targets and the bytes of every file, binary included)

Exit status 0 = clean, 1 = findings, 2 = usage / unreadable input. Findings are reported as `where:line: rule` plus a masked hint;
the secret itself is never printed. Besides credentials it flags
personal/location data: e-mail addresses (except hi@ptoq.io, example/test domains, git@ and noreply@) and public IPv4 addresses. A line containing `scan:allow` is skipped (for documented, deliberately fake examples).
"""
from __future__ import annotations

import gzip
import io
import math
import re
import sys
import tarfile
import zipfile
from pathlib import Path

MAX_FILE = 16 << 20
MAX_NESTED = 64 << 20

RULES = [
    ("aws-access-key-id", re.compile(rb"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b")),
    ("tencent-secret-id", re.compile(rb"\bAKID[A-Za-z0-9]{28,}\b")),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("github-fine-grained-token", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("openai-or-anthropic-style-key", re.compile(rb"\bsk-(?:proj-|ant-|live-|test-|svcacct-|admin-|or-v1-)?[A-Za-z0-9_\-]{20,}")),
    ("slack-token", re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}|\bxapp-[0-9]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(rb"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("private-key-block", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("google-api-key", re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-live-key", re.compile(rb"\b[sr]k_live_[0-9A-Za-z]{16,}")),
    ("legacy-project-token", re.compile(rb"\b(?:atr_[A-Za-z0-9_-]{20,}|ak_[A-Za-z0-9]{20,})")),
    ("llm-env-assignment", re.compile(rb"\bLLM_[A-Z0-9_]+_KEY\s*=\s*[^\s<$'\"{}][^\s]{7,}")),
    ("bearer-token", re.compile(rb"(?i)\bbearer[ \t]+(?!['\"+<${])[A-Za-z0-9._~+/=\-]{20,}")),
    ("credentials-in-url", re.compile(rb"\b[a-z][a-z0-9+.\-]*://[^/\s:@$<{]{2,}:[^/\s@$<{]{3,}@[A-Za-z0-9.\-]+")),
]
ASSIGN = re.compile(rb"(?i)\b[\w.\-]*(?:key|token|secret|passw(?:or)?d|credential|auth)[\w.\-]*[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9+/=_\-]{32,})[\"']?")
PLACEHOLDER = re.compile(rb"(?i)example|your|changeme|dummy|placeholder|xxxx|<|\$\{|redacted|0{8,}|(.)\1{9,}")
BAD_NAMES = re.compile(r"(^|/)(internal|\.git|\.ssh|\.aws|\.gnupg)(/|$)|(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$|"
                       r"(^|/)\.env(\.[^/]*)?$|\.env$|\.(pem|p12|pfx|key|keystore|jks|kdbx)$|\.sqlite[^/]*$|\.db$|"
                       r"(^|/)(\.netrc|\.npmrc|\.pypirc|\.pgpass|\.git-credentials|credentials(\.json)?|secrets?\.(json|ya?ml|toml))$|(^|/)\._[^/]*$|\.DS_Store$|(^|/)build(/|$)")


EMAIL = re.compile(rb"(?<![\w.%+\-])([A-Za-z0-9][A-Za-z0-9._%+\-]*)@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})\b")
EMAIL_OK = re.compile(rb"^(hi@ptoq\.io|[^@]+@(example|localhost|invalid|test)(\.[a-z]+)?|(git|noreply|no-reply)@.+|[^@]+@[^@]+\.(service|timer|socket|target|path|mount))$", re.I)
IPV4 = re.compile(rb"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")


def public_ipv4(m: re.Match, line: bytes) -> bool:
    o = [int(x) for x in m.groups()]
    if any(x > 255 for x in o):
        return False
    pre = line[max(0, m.start() - 3):m.start()]
    if pre.endswith((b"==", b">=", b"<=", b"~=", b"!=", b"v", b"V", b"-")):          # a version number (pkg==12.1.3.1), not an address
        return False
    a, b, c, _ = o
    if a in (0, 10, 127) or a >= 224 or (a, b) in ((192, 168), (169, 254)) or (a == 172 and 16 <= b <= 31) or (a == 100 and 64 <= b <= 127):
        return False
    return (a, b, c) not in ((192, 0, 2), (198, 51, 100), (203, 0, 113))              # documentation ranges


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in (b.count(x) for x in set(b)))


def scan_bytes(where: str, data: bytes) -> list[str]:
    out = []
    for no, line in enumerate(data.split(b"\n"), 1):
        if len(line) < 12 or b"scan:allow" in line:
            continue
        for name, rx in RULES:
            if rx.search(line):
                out.append(f"{where}:{no}: {name}")
        for m in EMAIL.finditer(line):
            if not EMAIL_OK.match(m.group(0)):
                out.append(f"{where}:{no}: e-mail address (personal data)")
        for m in IPV4.finditer(line):
            if public_ipv4(m, line):
                out.append(f"{where}:{no}: public IPv4 address (server location)")
        for m in ASSIGN.finditer(line):
            v = m.group(1)
            if not PLACEHOLDER.search(v) and entropy(v) >= 3.5 and not re.fullmatch(rb"[0-9a-f]{32,}", v) and not (b"/" in v and b"." in v):
                out.append(f"{where}:{no}: high-entropy value assigned to a key/token/secret/password name")
    return out


def check_name(name: str) -> list[str]:
    bad = []
    if name.startswith("/") or ".." in Path(name).parts:
        bad.append(f"{name}: absolute or parent-relative member name")
    if BAD_NAMES.search(name):
        bad.append(f"{name}: file that must never be published (secret-bearing name)")
    return bad


def scan_blob(where: str, data: bytes, depth: int = 0) -> list[str]:
    out = scan_bytes(where, data)
    if depth < 2 and data[:2] == b"\x1f\x8b":                       # a gzip inside the package: look inside
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
                out += scan_blob(where + "!gz", g.read(MAX_NESTED), depth + 1)
        except (OSError, EOFError):
            pass
    elif depth < 2 and data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for zi in z.infolist()[:2000]:
                    if not zi.is_dir() and zi.file_size <= MAX_FILE:
                        out += check_name(zi.filename) and [f"{where}!{zi.filename}: secret-bearing name"] or []
                        out += scan_blob(f"{where}!{zi.filename}", z.read(zi), depth + 1)
        except (zipfile.BadZipFile, RuntimeError):
            pass
    return out


def scan_tree(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out += check_name(rel)
        if p.is_symlink():
            tgt = str(p.readlink())
            if tgt.startswith("/") or ".." in Path(tgt).parts:
                out.append(f"{rel}: symlink pointing outside the package ({tgt[:60]})")
        elif p.is_file():
            if p.stat().st_size > MAX_FILE:
                out.append(f"{rel}: file larger than {MAX_FILE >> 20} MB (not scanned)")
                continue
            out += scan_blob(rel, p.read_bytes())
    return out


def scan_tar(path: Path) -> list[str]:
    out = []
    with tarfile.open(path, "r:*") as t:
        for m in t:
            out += check_name(m.name)
            if m.issym() or m.islnk():
                if m.linkname.startswith("/") or ".." in Path(m.linkname).parts:
                    out.append(f"{m.name}: link pointing outside the package ({m.linkname[:60]})")
            elif m.isdev() or m.isfifo():
                out.append(f"{m.name}: device or fifo member")
            elif m.isfile():
                if m.size > MAX_FILE:
                    out.append(f"{m.name}: file larger than {MAX_FILE >> 20} MB (not scanned)")
                    continue
                f = t.extractfile(m)
                out += scan_blob(m.name, f.read() if f else b"")
    return out


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] == "--tar":
            findings = scan_tar(Path(argv[2]))
        elif len(argv) == 2 and Path(argv[1]).is_dir():
            findings = scan_tree(Path(argv[1]))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (OSError, tarfile.TarError, EOFError) as e:
        print(f"cannot read input: {type(e).__name__}", file=sys.stderr)
        return 2
    for f in findings:
        print("SECRET-SCAN:", f, file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
