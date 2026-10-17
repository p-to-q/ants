"""OpenAI-compatible multi-endpoint client.

- Thompson-sampling choice between endpoints (reward = produced parseable JSON); falls back to the
  other endpoint on any failure.
- Models here are 'reasoning' models: they burn tokens thinking before answering, so max_tokens must
  be generous and timeouts long. Calls are cheap; evaluation is the scarce resource.
- API keys are read from an env file (mode 0600 enforced) and never logged or placed in exceptions.

Reliability (step 39):
- Endpoint health seeded from ledger (last 24 h) so the bandit does not relearn from scratch.
- Circuit breaker per endpoint: quarantine after N consecutive failures, exponential back-off.
- Retry with jitter for transient errors (429, 5xx, timeout, connection reset); Retry-After honoured.
- Per-arm lifetime budget (--llm-budget / ANTS_LLM_BUDGET).
- call_id + attempt columns group HTTP attempts into logical calls.
"""
from __future__ import annotations

import email.utils
import json
import os
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .textsafe import DECODER, HostileJSON, clean_text, depth_ok, loads_strict

MAX_RESPONSE_BYTES = 4 << 20        # a reply (reasoning included) is far smaller; a hostile or broken server must not fill our memory
MAX_REPLY_CHARS = 1 << 20           # the text we are willing to search for a JSON object
MAX_JSON_STARTS = 64                # '{' positions tried per reply: bounds the work on adversarial text like '{"a":1,{"a":1,...'
_LOOPBACK = ("localhost", "127.0.0.1", "::1")

# ---- retry constants --------------------------------------------------------
RETRY_MAX = 2                        # additional attempts (total = 1 + RETRY_MAX)
CALL_DEADLINE_S = 240                # one chat_json (all its attempts, all endpoints, all backoff) ends within this; under the 900 s of notify.keepalive and the unit's 300 s watchdog
RETRY_BASE_S = 1.0
RETRY_CAP_S = 20.0
RETRY_AFTER_CAP_S = 60.0

# ---- circuit breaker defaults -----------------------------------------------
CB_CONSECUTIVE = 5                   # trip after this many consecutive failures
CB_BASE_S = 600                      # initial quarantine: 10 min
CB_MAX_S = 7200                      # quarantine cap: 2 h


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
    # circuit breaker state
    _consec_fail: int = field(default=0, repr=False)
    _quarantine_until: float = field(default=0.0, repr=False)     # monotonic time
    _quarantine_duration: float = field(default=CB_BASE_S, repr=False)

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


class HTTPCallError(Exception):
    """Wraps an HTTP error with the status code and optional Retry-After header value, so retry logic can inspect them
    without parsing the error string.  The message never contains the API key (caller is responsible for that)."""
    def __init__(self, status: int, reason: str, retry_after: float | None = None):
        self.status, self.retry_after = status, retry_after
        super().__init__(f"HTTP Error {status}: {reason}")


class BodyTooSlow(TimeoutError):
    """The response body was dripped slowly past the deadline. Not retried: the server connected fine
    but is either hostile or serving huge content. Distinguished from a socket-level timeout."""
    pass


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (seconds or HTTP date). Returns seconds to wait, capped."""
    if not value:
        return None
    value = value.strip()
    try:
        s = float(value)
        return min(max(s, 0), RETRY_AFTER_CAP_S) if s == s else None   # NaN guard
    except ValueError:
        pass
    try:
        t = email.utils.parsedate_to_datetime(value).timestamp()
        delta = t - time.time()
        return min(max(delta, 0), RETRY_AFTER_CAP_S)
    except Exception:
        return None


def http_post_json(url: str, body: dict, headers: dict, timeout_s: float, max_bytes: int = MAX_RESPONSE_BYTES):
    """POST JSON, no redirects, response read in chunks with a size cap and a deadline, parsed strictly.
    Returns the object on success.  Raises HTTPCallError for HTTP errors (preserving status and Retry-After),
    and the original exception for network/TLS/timeout errors."""
    check_base(url)
    local = urllib.parse.urlsplit(url).scheme == "http"
    handlers = [_NoRedirect()] + ([urllib.request.ProxyHandler({})] if local else [urllib.request.HTTPSHandler(context=ssl_context())])
    req = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"})
    try:
        resp = urllib.request.build_opener(*handlers).open(req, timeout=timeout_s)
    except urllib.error.HTTPError as e:
        ra = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
        raise HTTPCallError(e.code, str(e.reason)[:200], ra) from e
    with resp as r:
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
                raise BodyTooSlow("response body too slow")
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


DEFAULT_DAILY_CALLS = 1500          # model calls (every endpoint attempt counts) per rolling 24 h; ANTS_LLM_MAX_CALLS_PER_DAY overrides, 0 = no cap


def daily_cap() -> int:
    try:
        n = int(float(os.environ.get("ANTS_LLM_MAX_CALLS_PER_DAY", DEFAULT_DAILY_CALLS)))
    except ValueError:
        return DEFAULT_DAILY_CALLS
    return n if n >= 0 else DEFAULT_DAILY_CALLS            # 0 = no cap, on purpose; a negative number is a typo, not a request for no cap


def _is_retryable(exc: Exception) -> bool:
    """True for transient errors worth retrying: 429, 5xx, timeouts, connection resets.
    NOT retryable: no-json (model answered with prose), 4xx other than 408/429, body-too-slow (hostile drip)."""
    if isinstance(exc, HTTPCallError):
        return exc.status == 429 or exc.status == 408 or exc.status >= 500
    if isinstance(exc, BodyTooSlow):
        return False                         # connected fine, body dripped: retrying would repeat the same slow read
    name = type(exc).__name__
    # TimeoutError, ConnectionResetError, RemoteDisconnected, socket.timeout, OSError for broken pipe
    if isinstance(exc, (TimeoutError, ConnectionResetError, BrokenPipeError)):
        return True
    if "RemoteDisconnected" in name or "ConnectionAborted" in name:
        return True
    if isinstance(exc, OSError) and exc.errno in (104, 110, 111):   # ECONNRESET, ETIMEDOUT, ECONNREFUSED on Linux
        return True
    return False


def _retry_delay(attempt: int, rng: random.Random, retry_after: float | None = None) -> float:
    """Exponential backoff with full jitter, honouring Retry-After when present."""
    base = min(RETRY_BASE_S * (2 ** attempt), RETRY_CAP_S)
    jitter = rng.uniform(0, base)
    if retry_after is not None and retry_after > jitter:
        return retry_after
    return jitter


def seed_endpoint_health(endpoints: list[Endpoint], ledger, window_s: float = 86400):
    """Seed each endpoint's ok/bad from the ledger so the bandit does not relearn from scratch after a restart.
    The model field in llm_calls is '<endpoint name>:<model>'."""
    if not ledger:
        return
    since = time.time() - window_s
    try:
        rows = ledger.conn.execute("SELECT model, ok, COUNT(*) FROM llm_calls WHERE ts>=? GROUP BY model, ok", (since,)).fetchall()
    except Exception:
        return
    by_ep: dict[str, dict[str, int]] = {}
    for model_str, ok_val, cnt in rows:
        if not model_str or ":" not in model_str:
            continue
        ep_name = model_str.split(":")[0]
        d = by_ep.setdefault(ep_name, {"ok": 0, "bad": 0})
        if ok_val:
            d["ok"] += cnt
        else:
            d["bad"] += cnt
    for ep in endpoints:
        if ep.name in by_ep:
            ep.ok = by_ep[ep.name]["ok"]
            ep.bad = by_ep[ep.name]["bad"]


class LLMClient:
    def __init__(self, endpoints: list[Endpoint], ledger=None, timeout_s: float = 100,
                 max_tokens: int = 4000, rng: random.Random | None = None, daily_calls: int | None = None,
                 budget: int = 0, arm: str = "", clock=None, stop=None, call_deadline_s: float = CALL_DEADLINE_S,
                 cb_consecutive: int = CB_CONSECUTIVE, cb_base_s: float = CB_BASE_S, cb_max_s: float = CB_MAX_S):
        self.eps, self.ledger, self.timeout_s, self.max_tokens = endpoints, ledger, timeout_s, max_tokens
        self.daily_calls = daily_cap() if daily_calls is None else daily_calls
        self.budget = budget                                     # 0 = no lifetime budget
        self.arm = arm                                           # recorded in llm_calls.arm
        self.rng = rng or random.Random(0)
        self._lock = threading.Lock()
        self._inflight = 0                                       # calls started but not yet in the ledger: counted, so concurrent callers cannot all pass the check
        self._call_seq = 0                                       # monotonic call_id within this client instance
        self._clock = clock or time.monotonic                    # injectable for tests
        self._sleep = time.sleep                                 # injectable for tests
        self._stop = stop or (lambda: False)                     # shutdown check
        self.call_deadline_s = call_deadline_s
        self._tls = threading.local()                            # the timeout of the attempt in progress on this thread
        self._cb_consecutive = cb_consecutive
        self._cb_base_s = cb_base_s
        self._cb_max_s = cb_max_s
        self._budget_event_written = False
        for ep in endpoints:
            ep._quarantine_duration = cb_base_s    # align with client config
        seed_endpoint_health(endpoints, ledger)

    def set_stop(self, stop) -> None:
        """the daemon's shutdown flag, known only after the client was built: backoff and further attempts end as soon as it is true"""
        self._stop = stop

    # overridable for tests
    def _post(self, ep: Endpoint, body: dict) -> dict:
        timeout = getattr(self._tls, "timeout", None) or self.timeout_s
        return http_post_json(ep.base + "/chat/completions", body, {"Authorization": "Bearer " + ep.key}, timeout)

    def _next_call_id(self) -> int:
        with self._lock:
            self._call_seq += 1
            return self._call_seq

    # ---- circuit breaker ----------------------------------------------------
    def _cb_is_open(self, ep: Endpoint) -> bool:
        return ep._quarantine_until > 0 and self._clock() < ep._quarantine_until

    def _cb_record_success(self, ep: Endpoint):
        with self._lock:
            was_quarantined = ep._quarantine_until > 0
            ep._consec_fail = 0
            ep._quarantine_until = 0.0
            ep._quarantine_duration = self._cb_base_s    # reset backoff
        if was_quarantined:
            self._cb_event(ep, "recovered", "success after quarantine")

    def _cb_record_failure(self, ep: Endpoint):
        with self._lock:
            ep._consec_fail += 1
            if ep._consec_fail >= self._cb_consecutive and not self._cb_is_open(ep):
                dur = min(ep._quarantine_duration, self._cb_max_s)
                ep._quarantine_until = self._clock() + dur
                ep._quarantine_duration = min(dur * 2, self._cb_max_s)
                until_wall = time.time() + dur
                self._cb_event(ep, "quarantined", f"{ep._consec_fail} consecutive failures", until_wall)

    def _cb_event(self, ep: Endpoint, state: str, reason: str, until: float | None = None):
        """Write a ledger event for state changes only. Low volume: only on trip and recovery."""
        if not self.ledger:
            return
        try:
            payload = {"endpoint": ep.name, "state": state, "reason": reason}
            if until is not None:
                payload["until"] = until
            self.ledger.event("llm_endpoint_state", payload)
        except Exception:
            pass

    def _all_quarantined(self) -> bool:
        now = self._clock()
        return all(ep._quarantine_until > 0 and now < ep._quarantine_until for ep in self.eps)

    def _pick_order(self) -> list[Endpoint]:
        """Thompson sampling, skipping quarantined endpoints (unless all are quarantined)."""
        all_q = self._all_quarantined()
        with self._lock:
            draws = [(self.rng.betavariate(e.ok + 1, e.bad + 1), e) for e in self.eps
                     if all_q or not self._cb_is_open(e)]
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

    def _interruptible_sleep(self, seconds: float):
        """Sleep that wakes early if the process is shutting down. Returns True if interrupted."""
        end = self._clock() + seconds
        while (left := end - self._clock()) > 0:
            if self._stop():
                return True
            self._sleep(min(0.25, left))
        return False

    def _attempt_one(self, ep: Endpoint, messages: list[dict], temperature: float) -> tuple[bool, str, int | None, dict | None]:
        """Make one HTTP attempt.  Returns (ok, note, tokens, obj)."""
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
            return ok, note, toks, obj
        except Exception as e:
            raise

    def chat_json(self, messages: list[dict], temperature: float = 0.9):
        """Returns (obj|None, meta). Tries endpoints in bandit order; first parseable JSON wins. Never raises: whatever the
        server (or the ledger) does, the caller gets (None, meta) at worst."""
        meta = {"attempts": []}

        # all endpoints quarantined: return paused without spending a call
        if self.eps and self._all_quarantined():
            earliest = min(ep._quarantine_until for ep in self.eps)
            meta["paused"] = True
            meta["until"] = time.time() + max(0, earliest - self._clock())
            return None, meta

        call_id = self._next_call_id()
        deadline = self._clock() + self.call_deadline_s

        for ep in self._pick_order():
            # try this endpoint with retries for transient errors
            for attempt_num in range(1 + RETRY_MAX):
                left = deadline - self._clock()
                if left <= 5 or self._stop():                # the whole call is bounded: see CALL_DEADLINE_S
                    meta["deadline" if left <= 5 else "stopped"] = True
                    return None, meta
                if not self._reserve():                      # both caps, one reservation, one critical section
                    meta["capped"] = True
                    return None, meta
                t = time.time()
                ok, note, toks, obj = False, "", None, None
                last_exc = None
                try:
                    self._tls.timeout = min(self.timeout_s, max(left, 1.0))     # an attempt never outlives the call's deadline by more than a few seconds
                    try:
                        ok, note, toks, obj = self._attempt_one(ep, messages, temperature)
                    except Exception as e:
                        note = self._note(ep, e)
                        last_exc = e
                    dt = time.time() - t
                    with self._lock:
                        ep.ok += ok; ep.bad += (not ok); ep.lat = (ep.lat + [dt])[-1000:]
                    if self.ledger:
                        try:
                            self.ledger.log_llm(clean_text(f"{ep.name}:{ep.model}", 80), ok, toks, note,
                                                call_id=call_id, attempt=attempt_num + 1, arm=self.arm or None)
                        except Exception:
                            pass
                finally:
                    self._tls.timeout = None
                    self._release()                          # exactly once per reservation, whatever escapes (a Shutdown raised by the signal handler included)
                meta["attempts"].append({"ep": ep.name, "ok": ok, "s": round(dt, 1), "note": note})

                if ok:
                    self._cb_record_success(ep)
                    meta["ep"] = ep.name
                    return obj, meta

                # no-json (HTTP 200 with prose) is a model failure, not a transport failure:
                # it does NOT trip the circuit breaker and is NOT retried
                transport_failure = last_exc is not None

                # decide whether to retry this endpoint
                if transport_failure and _is_retryable(last_exc) and attempt_num < RETRY_MAX:
                    self._cb_record_failure(ep)
                    if self._stop():
                        return None, meta
                    ra = getattr(last_exc, "retry_after", None) if isinstance(last_exc, HTTPCallError) else None
                    delay = min(_retry_delay(attempt_num, self.rng, ra), max(0.0, deadline - self._clock() - 5))
                    if self._interruptible_sleep(delay):
                        return None, meta         # shutdown during backoff
                    continue

                # transport failure (not retryable or retries exhausted): record for circuit breaker
                if transport_failure:
                    self._cb_record_failure(ep)
                break

        return None, meta

    def _reserve(self) -> bool:
        """Check both caps and take one reservation in ONE critical section (several proposer threads may be here at once; a check
        and an increment in two steps lets all of them pass at cap - 1). False = capped; the caller must `_release()` after the attempt."""
        with self._lock:
            try:
                if self.ledger and self.daily_calls and self.ledger.llm_calls_since(time.time() - 86400) + self._inflight >= self.daily_calls:
                    return False
                if self.ledger and self.budget and self._budget_used_locked() + self._inflight >= self.budget:
                    return False
            except Exception:                                # a ledger that cannot be read must not take the proposer down (see test_security_llm)
                pass
            self._inflight += 1
            return True

    def _release(self) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    def _budget_used_locked(self) -> int:
        """HTTP attempts this arm has spent in its life. Rows written before the `arm` column existed (schema v1) carry no arm; the ledger
        belongs to one arm, so they count too."""
        total = self.ledger.llm_calls_since(0, arm=self.arm) if self.arm else 0
        total += self.ledger.llm_calls_since(0, arm=None, unlabelled_only=True) if self.arm else 0
        if total + self._inflight >= self.budget and not self._budget_event_written:
            self._budget_event_written = True
            try:
                self.ledger.event("llm_budget_reached", {"arm": self.arm, "budget": self.budget, "used": total})
            except Exception:
                pass
        return total

    def _capped(self) -> bool:
        """not used by chat_json any more (see _reserve); kept for callers that only want to know"""
        with self._lock:
            try:
                return bool(self.ledger and self.daily_calls and self.ledger.llm_calls_since(time.time() - 86400) + self._inflight >= self.daily_calls)
            except Exception:
                return False

    def _budget_reached(self) -> bool:
        with self._lock:
            try:
                return bool(self.ledger and self.budget and self._budget_used_locked() + self._inflight >= self.budget)
            except Exception:
                return False

    def stats(self) -> dict:
        out = {}
        for e in self.eps:
            d = {"ok": e.ok, "bad": e.bad,
                 "p50_s": (sorted(e.lat)[len(e.lat) // 2] if e.lat else None)}
            if self._cb_is_open(e):
                d["quarantined_until"] = time.time() + max(0, e._quarantine_until - self._clock())
            d["consec_fail"] = e._consec_fail
            out[e.name] = d
        return out
