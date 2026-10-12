"""OpenAI-compatible multi-endpoint client.

- Thompson-sampling choice between endpoints (reward = produced parseable JSON); falls back to the
  other endpoint on any failure.
- Models here are 'reasoning' models: they burn tokens thinking before answering, so max_tokens must
  be generous and timeouts long. Calls are cheap; evaluation is the scarce resource.
- API keys are read from an env file (mode 0600 enforced) and never logged or placed in exceptions.
"""
from __future__ import annotations

import json
import os
import random
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .textsafe import DECODER, HostileJSON, clean_text, depth_ok, loads_strict

MAX_RESPONSE_BYTES = 4 << 20        # a reply (reasoning included) is far smaller; a hostile or broken server must not fill our memory
MAX_REPLY_CHARS = 1 << 20           # the text we are willing to search for a JSON object
MAX_JSON_STARTS = 64                # '{' positions tried per reply: bounds the work on adversarial text like '{"a":1,{"a":1,...'
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


@dataclass
class Endpoint:
    name: str
    base: str
    key: str = field(repr=False)
    model: str
    conc: int = 3
    ok: int = 0
    bad: int = 0
    lat: list = field(default_factory=list)

    def __post_init__(self):
        self.sem = threading.Semaphore(self.conc)


_CA_CANDIDATES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def ssl_context() -> ssl.SSLContext:
    """TLS verification is NEVER disabled. Some Python builds (python.org macOS) ship without a CA bundle: fall back to certifi or
    the operating system's bundle, and fail with a clear message if none exists."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) > 0:
        return ctx
    paths = []
    try:
        import certifi
        paths.append(certifi.where())
    except ImportError:
        pass
    for pth in paths + list(_CA_CANDIDATES):
        if os.path.exists(pth):
            return ssl.create_default_context(cafile=pth)
    raise RuntimeError("no CA certificate bundle found (install certifi or the OS ca-certificates package); refusing to disable TLS verification")


def extract_json(text: str):
    """First JSON object in text (tolerates ```json fences and prose around it). The text is untrusted: it never raises, reads at most
    MAX_REPLY_CHARS, tries at most MAX_JSON_STARTS positions, and refuses NaN / Infinity / duplicate keys / deep nesting (-> None)."""
    if not isinstance(text, str):
        return None
    text = text[:MAX_REPLY_CHARS]
    i, tries = text.find("{"), 0
    while i != -1 and tries < MAX_JSON_STARTS:
        tries += 1
        try:
            obj, _ = DECODER.raw_decode(text, i)
        except HostileJSON:
            return None
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(obj, dict):
                return obj if depth_ok(obj) else None
        i = text.find("{", i + 1)
    return None


def check_base(base: str) -> str:
    """The API key travels in a header: https only (plain http only to this machine), no credentials / query in the URL."""
    u = urllib.parse.urlsplit(base)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in _LOOPBACK):
        if u.hostname and not u.username and not u.password and not u.query and not u.fragment:
            return base
    raise ValueError("endpoint base must be https (http only for localhost) with no credentials, query or fragment")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header to wherever a 3xx points. Never follow: the reply is an error instead."""
    def redirect_request(self, *a, **k):
        return None


def http_post_json(url: str, body: dict, headers: dict, timeout_s: float, max_bytes: int = MAX_RESPONSE_BYTES):
    """POST JSON, no redirects, response read in chunks with a size cap and a deadline, parsed strictly. Returns the object."""
    check_base(url)
    local = urllib.parse.urlsplit(url).scheme == "http"
    handlers = [_NoRedirect()] + ([urllib.request.ProxyHandler({})] if local else [urllib.request.HTTPSHandler(context=ssl_context())])
    req = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"})
    with urllib.request.build_opener(*handlers).open(req, timeout=timeout_s) as r:
        declared = r.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(f"response too large ({declared} bytes)")
        deadline, buf = time.monotonic() + timeout_s, bytearray()
        while True:
            chunk = r.read1(min(1 << 16, max_bytes + 1 - len(buf)))     # read1: returns what has arrived, so the deadline is checked
            if not chunk:
                break
            buf += chunk
            if len(buf) > max_bytes:
                raise ValueError("response too large")
            if time.monotonic() > deadline:
                raise TimeoutError("response body too slow")
    return loads_strict(bytes(buf), max_chars=max_bytes)


def load_endpoints(env_path, models: dict | None = None, conc: int = 3) -> list[Endpoint]:
    """env file lines: LLM_<TAG>_BASE, LLM_<TAG>_KEY, LLM_<TAG>_MODEL (the model id is configuration, never hard-coded)."""
    p = Path(env_path).expanduser()
    with open(p) as fh:                                          # check the mode of the file we actually read (no stat/open race)
        if os.fstat(fh.fileno()).st_mode & 0o077:
            raise PermissionError(f"{p} must be chmod 600 (it holds API keys)")
        text = fh.read(1 << 20)
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            k, v = k.strip().removeprefix("export ").strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":        # KEY="abc" and KEY='abc' mean the same as KEY=abc (as in systemd / shell)
                v = v[1:-1]
            env[k] = v
    tags = sorted({k.split("_")[1] for k in env if k.startswith("LLM_") and k.endswith("_BASE")})
    eps = []
    for tag in tags:
        model = (models or {}).get(tag) or env.get(f"LLM_{tag}_MODEL")
        if not model:
            raise ValueError(f"LLM_{tag}_MODEL is missing in {p}: the model id must be configured explicitly")
        eps.append(Endpoint(tag, env[f"LLM_{tag}_BASE"].rstrip("/"), env[f"LLM_{tag}_KEY"], model, conc))
    return eps


class LLMClient:
    def __init__(self, endpoints: list[Endpoint], ledger=None, timeout_s: float = 100,
                 max_tokens: int = 4000, rng: random.Random | None = None):
        self.eps, self.ledger, self.timeout_s, self.max_tokens = endpoints, ledger, timeout_s, max_tokens
        self.rng = rng or random.Random(0)
        self._lock = threading.Lock()

    # overridable for tests
    def _post(self, ep: Endpoint, body: dict) -> dict:
        return http_post_json(ep.base + "/chat/completions", body, {"Authorization": "Bearer " + ep.key}, self.timeout_s)

    def _pick_order(self) -> list[Endpoint]:
        with self._lock:
            draws = [(self.rng.betavariate(e.ok + 1, e.bad + 1), e) for e in self.eps]
        return [e for _, e in sorted(draws, key=lambda t: -t[0])]

    @staticmethod
    def _note(ep: Endpoint, e: BaseException) -> str:
        """Why a call failed, safe to store and print: the key is removed BEFORE truncating (a key cut in half would escape the
        replace), and control characters from a hostile server's error text are made visible."""
        text = f"{type(e).__name__}: {e}"
        for secret in (ep.key,):
            if secret:
                text = text.replace(secret, "***")
        return clean_text(text, 160)

    def chat_json(self, messages: list[dict], temperature: float = 0.9):
        """Returns (obj|None, meta). Tries endpoints in bandit order; first parseable JSON wins. Never raises: whatever the
        server (or the ledger) does, the caller gets (None, meta) at worst."""
        meta = {"attempts": []}
        for ep in self._pick_order():
            t = time.time()
            ok, note, toks, obj = False, "", None, None
            try:
                with ep.sem:
                    d = self._post(ep, {"model": ep.model, "messages": messages,
                                        "max_tokens": self.max_tokens, "temperature": temperature})
                msg = d["choices"][0]["message"]
                u = d.get("usage")
                n = u.get("completion_tokens") if isinstance(u, dict) else None
                toks = n if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 1 << 31 else None
                content = msg.get("content")
                obj = extract_json(content if isinstance(content, str) else "")
                ok = obj is not None
                note = "" if ok else "no-json"
            except Exception as e:                       # network, HTTP, TLS, malformed or hostile body: keep the reason (never the key)
                note = self._note(ep, e)
            dt = time.time() - t
            with self._lock:
                ep.ok += ok; ep.bad += (not ok); ep.lat = (ep.lat + [dt])[-1000:]
            if self.ledger:
                try:
                    self.ledger.log_llm(clean_text(f"{ep.name}:{ep.model}", 80), ok, toks, note)
                except Exception:                        # the audit row is best-effort; a full disk must not kill the proposer thread
                    pass
            meta["attempts"].append({"ep": ep.name, "ok": ok, "s": round(dt, 1), "note": note})
            if ok:
                meta["ep"] = ep.name
                return obj, meta
        return None, meta

    def stats(self) -> dict:
        return {e.name: {"ok": e.ok, "bad": e.bad,
                         "p50_s": (sorted(e.lat)[len(e.lat) // 2] if e.lat else None)} for e in self.eps}
