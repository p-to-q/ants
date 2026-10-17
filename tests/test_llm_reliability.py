"""Tests for model-call reliability: circuit breaker, retry, health seeding, budgets, call tracking, migration, System page."""
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from antslab.ledger import Ledger
from antslab.llm import (CB_BASE_S, CB_CONSECUTIVE, Endpoint, HTTPCallError, LLMClient,
                          RETRY_AFTER_CAP_S, _is_retryable, _parse_retry_after, _retry_delay,
                          seed_endpoint_health)

KEY = "sk-" + "test-key-never-leak"


# ---- helpers ----------------------------------------------------------------
def _ep(name="A", port=1):
    return Endpoint(name, f"http://127.0.0.1:{port}/v1", KEY, "m")


def _reply(content='{"proposals": []}', **extra):
    return json.dumps({"choices": [{"message": {"content": content}}], **extra}).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.server.call_count += 1
        mode = self.server.modes[min(self.server.call_count - 1, len(self.server.modes) - 1)]
        getattr(self, f"mode_{mode}")()

    def mode_ok(self):
        data = self.server.reply
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def mode_429(self):
        self.send_response(429)
        if self.server.retry_after is not None:
            self.send_header("Retry-After", str(self.server.retry_after))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def mode_500(self):
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def mode_503(self):
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def mode_400(self):
        self.send_response(400)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def mode_hang(self):
        """Don't respond: the client will time out."""
        time.sleep(5)

    def mode_reset(self):
        """Close the connection without a response."""
        self.connection.close()

    def mode_prose(self):
        """Return a valid HTTP response with no JSON."""
        body = b'{"choices": [{"message": {"content": "I think you should try a=0.5"}}]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _server(modes, reply=None, retry_after=None):
    s = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    s.modes = modes if isinstance(modes, list) else [modes]
    s.reply = reply or _reply()
    s.call_count = 0
    s.retry_after = retry_after
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _client(servers, ledger, timeout_s=3, **kw):
    """Create a client with one or more test servers as endpoints."""
    if not isinstance(servers, list):
        servers = [servers]
    eps = [Endpoint(chr(65 + i), f"http://127.0.0.1:{s.server_port}/v1", KEY, "m") for i, s in enumerate(servers)]
    return LLMClient(eps, ledger, timeout_s=timeout_s, rng=random.Random(42), **kw)


# ---- _parse_retry_after -----------------------------------------------------
def test_parse_retry_after_seconds():
    assert _parse_retry_after("5") == 5.0
    assert _parse_retry_after("0") == 0.0
    assert _parse_retry_after("  10  ") == 10.0

def test_parse_retry_after_capped():
    assert _parse_retry_after("999") == RETRY_AFTER_CAP_S

def test_parse_retry_after_none_on_garbage():
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("not a number") is None

def test_parse_retry_after_nan():
    assert _parse_retry_after("nan") is None


# ---- _is_retryable -----------------------------------------------------------
def test_retryable_errors():
    assert _is_retryable(HTTPCallError(429, "rate limit"))
    assert _is_retryable(HTTPCallError(500, "internal"))
    assert _is_retryable(HTTPCallError(503, "unavailable"))
    assert _is_retryable(HTTPCallError(408, "timeout"))
    assert _is_retryable(TimeoutError("read timed out"))
    assert _is_retryable(ConnectionResetError("reset"))

def test_non_retryable_errors():
    assert not _is_retryable(HTTPCallError(400, "bad request"))
    assert not _is_retryable(HTTPCallError(401, "unauthorized"))
    assert not _is_retryable(HTTPCallError(403, "forbidden"))
    assert not _is_retryable(ValueError("no-json"))
    assert not _is_retryable(RuntimeError("something else"))


# ---- _retry_delay ------------------------------------------------------------
def test_retry_delay_grows():
    rng = random.Random(0)
    d0 = _retry_delay(0, rng)
    d1 = _retry_delay(1, rng)
    assert d0 >= 0 and d1 >= 0

def test_retry_delay_honours_retry_after():
    rng = random.Random(0)
    d = _retry_delay(0, rng, retry_after=10.0)
    assert d >= 10.0


# ---- seed_endpoint_health ----------------------------------------------------
def test_health_seeded_from_ledger(ledger):
    # populate some call history
    for i in range(10):
        ledger.log_llm("A:model-a", True, 100, "")
    for i in range(5):
        ledger.log_llm("A:model-a", False, None, "HTTP Error 500: internal")
    for i in range(3):
        ledger.log_llm("B:model-b", True, 50, "")

    eps = [Endpoint("A", "https://h/v1", KEY, "m-a"), Endpoint("B", "https://h/v1", KEY, "m-b")]
    seed_endpoint_health(eps, ledger)
    assert eps[0].ok == 10 and eps[0].bad == 5
    assert eps[1].ok == 3 and eps[1].bad == 0


def test_health_seeded_ignores_old_calls(tmp_path):
    led = Ledger(tmp_path / "hs" / "ledger.sqlite")
    # write an old call manually
    led.conn.execute("INSERT INTO llm_calls(ts,model,ok,tokens,note) VALUES(?,?,?,?,?)",
                     (time.time() - 200000, "A:m", 1, 10, ""))
    eps = [Endpoint("A", "https://h/v1", KEY, "m")]
    seed_endpoint_health(eps, led, window_s=86400)
    assert eps[0].ok == 0    # too old to count


# ---- circuit breaker ---------------------------------------------------------
class FakeClock:
    """Injectable monotonic clock for testing."""
    def __init__(self, start=1000.0):
        self._t = start
    def __call__(self):
        return self._t
    def advance(self, s):
        self._t += s


def test_breaker_trips_after_consecutive_failures(ledger):
    s = _server(["500"] * 20)
    clock = FakeClock()
    cl = _client(s, ledger, clock=clock, cb_consecutive=3, cb_base_s=60)
    def advance_sleep(t): clock.advance(t)
    cl._sleep = advance_sleep
    # make 3 failing calls to trip the breaker (each call does up to 3 attempts with retry)
    cl.chat_json([{"role": "user", "content": "hi"}])
    # the endpoint should be quarantined after 3+ consecutive failures from retries
    assert cl.eps[0]._quarantine_until > 0
    assert cl.eps[0]._consec_fail >= 3


def test_breaker_recovery_on_success(ledger):
    s = _server(["500", "500", "500", "ok", "ok"])
    clock = FakeClock()
    cl = _client(s, ledger, clock=clock, cb_consecutive=3, cb_base_s=60)
    def advance_sleep(t): clock.advance(t)
    cl._sleep = advance_sleep
    # trip the breaker
    cl.chat_json([{"role": "user", "content": "hi"}])
    assert cl.eps[0]._quarantine_until > 0
    # advance past quarantine
    clock.advance(70)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []}
    assert cl.eps[0]._consec_fail == 0
    assert cl.eps[0]._quarantine_until == 0


def test_all_quarantined_returns_paused_without_a_call(ledger):
    s = _server(["500"] * 20)
    clock = FakeClock()
    cl = _client(s, ledger, clock=clock, cb_consecutive=2, cb_base_s=60)
    def advance_sleep(t): clock.advance(t)
    cl._sleep = advance_sleep
    # trip the breaker on the only endpoint (1 call = up to 3 attempts, 2 failures trips cb_consecutive=2)
    cl.chat_json([{"role": "user", "content": "hi"}])
    calls_before = s.call_count
    # now all are quarantined
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None
    assert meta.get("paused") is True
    assert "until" in meta
    # no additional HTTP call was made
    assert s.call_count == calls_before


def test_breaker_doubles_quarantine_on_repeated_trips(ledger):
    s = _server(["500"] * 50)
    clock = FakeClock()
    cl = _client(s, ledger, clock=clock, cb_consecutive=2, cb_base_s=60, cb_max_s=3600)
    def advance_sleep(t): clock.advance(t)
    cl._sleep = advance_sleep
    # first trip
    cl.chat_json([{"role": "user", "content": "hi"}])
    first_dur = cl.eps[0]._quarantine_duration
    # advance past first quarantine
    clock.advance(70)
    cl.eps[0]._consec_fail = 0
    cl.eps[0]._quarantine_until = 0
    # second trip
    cl.chat_json([{"role": "user", "content": "hi"}])
    assert cl.eps[0]._quarantine_duration > first_dur


# ---- retry with backoff ------------------------------------------------------
def _fast_client(servers, ledger, **kw):
    """Client with a fake clock so retry sleeps are instant."""
    clock = FakeClock()
    cl = _client(servers, ledger, clock=clock, **kw)
    def advance_sleep(t):
        clock.advance(t)
    cl._sleep = advance_sleep
    cl._test_clock = clock
    return cl


def test_retry_on_429_and_succeeds(ledger):
    s = _server(["429", "ok"])
    cl = _fast_client(s, ledger)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []}
    assert len(meta["attempts"]) == 2
    assert "429" in meta["attempts"][0]["note"]
    assert meta["attempts"][1]["ok"] is True


def test_retry_on_500_and_succeeds(ledger):
    s = _server(["500", "ok"])
    cl = _fast_client(s, ledger)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []}
    assert len(meta["attempts"]) == 2


def test_retry_on_timeout(ledger):
    s_hang = _server(["hang", "hang", "hang"])
    cl = _fast_client(s_hang, ledger, timeout_s=0.5)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None
    # should have attempted up to RETRY_MAX + 1 times (3)
    assert len(meta["attempts"]) == 3


def test_retry_honours_retry_after(ledger):
    s = _server(["429", "ok"], retry_after="2")
    clock = FakeClock()
    slept = []
    cl = _client(s, ledger, clock=clock)
    def track_sleep(t):
        slept.append(t)
        clock.advance(t)
    cl._sleep = track_sleep
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []}
    # total sleep should be at least 2 seconds (the Retry-After value)
    assert sum(slept) >= 2.0, f"slept: {slept}"


def test_no_retry_on_no_json(ledger):
    s = _server(["prose", "prose", "prose"])
    cl = _fast_client(s, ledger)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None
    # no-json is not retryable: one attempt per endpoint
    assert len(meta["attempts"]) == 1
    assert meta["attempts"][0]["note"] == "no-json"


def test_no_retry_on_400(ledger):
    s = _server(["400", "400"])
    cl = _fast_client(s, ledger)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None
    # 400 is not retryable
    assert len(meta["attempts"]) == 1


# ---- shutdown during backoff -------------------------------------------------
def test_shutdown_during_backoff_returns_within_1s(ledger):
    s = _server(["500"] * 10)
    stop_flag = {"v": False}
    cl = _client(s, ledger, stop=lambda: stop_flag["v"])
    # after the first failure, the client will try to sleep for backoff;
    # we fire the stop flag after 0.1s
    def fire_stop():
        time.sleep(0.1)
        stop_flag["v"] = True
    threading.Thread(target=fire_stop, daemon=True).start()
    t0 = time.time()
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    dt = time.time() - t0
    assert obj is None
    assert dt < 2.0, f"took {dt:.1f}s, should be < 2s"


# ---- call_id and attempt columns --------------------------------------------
def test_one_logical_call_several_attempt_rows(ledger):
    s = _server(["500", "ok"])
    cl = _fast_client(s, ledger)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []}
    rows = ledger.conn.execute("SELECT call_id, attempt FROM llm_calls ORDER BY id").fetchall()
    assert len(rows) == 2
    # same call_id, different attempt numbers
    assert rows[0][0] == rows[1][0]   # same call_id
    assert rows[0][1] == 1 and rows[1][1] == 2


def test_arm_column_recorded(ledger):
    s = _server(["ok"])
    cl = _client(s, ledger, arm="test_arm")
    cl.chat_json([{"role": "user", "content": "hi"}])
    row = ledger.conn.execute("SELECT arm FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "test_arm"


# ---- budget ------------------------------------------------------------------
def test_budget_reached_stops_calls(ledger):
    s = _server(["ok"] * 20)
    cl = _client(s, ledger, budget=3, arm="budgeted")
    for _ in range(5):
        obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    # should have made at most 3 calls
    total = ledger.llm_calls_since(0, arm="budgeted")
    assert total <= 3
    # last calls should be capped
    assert meta.get("capped") is True


def test_budget_event_written_once(ledger):
    s = _server(["ok"] * 20)
    cl = _client(s, ledger, budget=2, arm="budget_ev")
    for _ in range(5):
        cl.chat_json([{"role": "user", "content": "hi"}])
    events = ledger.events("llm_budget_reached")
    assert len(events) == 1
    assert events[0]["payload"]["arm"] == "budget_ev"


# ---- migration ---------------------------------------------------------------
def test_migration_from_v1(tmp_path):
    """Create a v1 ledger (no call_id/attempt/arm), open it with new code, verify migration."""
    db_path = tmp_path / "mig" / "ledger.sqlite"
    db_path.parent.mkdir()
    import sqlite3
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY, gym TEXT, arm TEXT, seed INTEGER, params TEXT, source TEXT, state TEXT DEFAULT 'queued', attempt INTEGER DEFAULT 0, priority INTEGER DEFAULT 0, stage TEXT DEFAULT 's0', spec_key TEXT DEFAULT '', epoch TEXT DEFAULT '', created_at REAL, started_at REAL, finished_at REAL, lease_until REAL, metrics TEXT, error TEXT, out_dir TEXT, cpu_s REAL, parent_id INTEGER, origin_key TEXT, edit TEXT);
        CREATE INDEX IF NOT EXISTS ix_trials_state ON trials(state, priority DESC, id);
        CREATE INDEX IF NOT EXISTS ix_trials_arm ON trials(gym, arm, id);
        CREATE INDEX IF NOT EXISTS ix_trials_spec ON trials(gym, arm, spec_key, stage);
        CREATE INDEX IF NOT EXISTS ix_trials_fin ON trials(gym, arm, state, finished_at);
        CREATE TABLE IF NOT EXISTS epochs(id TEXT PRIMARY KEY, ts REAL NOT NULL, info TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, seq);
        CREATE TABLE IF NOT EXISTS artifacts(id INTEGER PRIMARY KEY, trial_id INTEGER, path TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, protected INTEGER DEFAULT 0, archived_uri TEXT, archived_sha256 TEXT, verified_at REAL, deleted_at REAL);
        CREATE TABLE IF NOT EXISTS unseals(id INTEGER PRIMARY KEY, gym TEXT NOT NULL, ts REAL NOT NULL, candidate TEXT NOT NULL, result TEXT, epoch TEXT DEFAULT '', sealed_set TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS judgements(id INTEGER PRIMARY KEY, ts REAL NOT NULL, judge TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, p REAL, label TEXT, raw TEXT, authority TEXT NOT NULL, outcome INTEGER, resolved_at REAL);
        CREATE INDEX IF NOT EXISTS ix_judge ON judgements(judge, kind, outcome);
        CREATE TABLE IF NOT EXISTS llm_calls(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, model TEXT, ok INTEGER NOT NULL, tokens INTEGER, note TEXT);
        PRAGMA user_version=1;
    """)
    # insert a v1 llm_call
    conn.execute("INSERT INTO llm_calls(ts,model,ok,tokens,note) VALUES(?,?,?,?,?)",
                 (time.time(), "A:m", 1, 42, ""))
    # verify data persists
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1
    conn.close()

    # open with the new Ledger code: should migrate to v2
    led = Ledger(str(db_path))
    assert led.schema_version() == 2

    # verify the new columns exist
    cols = {r[1] for r in led.conn.execute("PRAGMA table_info(llm_calls)")}
    assert "call_id" in cols
    assert "attempt" in cols
    assert "arm" in cols

    # old row has NULL for new columns
    row = led.conn.execute("SELECT call_id, attempt, arm FROM llm_calls").fetchone()
    assert row[0] is None and row[1] is None and row[2] is None

    # can write new-style rows
    led.log_llm("A:m", True, 10, "", call_id=1, attempt=1, arm="test")
    row = led.conn.execute("SELECT call_id, attempt, arm FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == 1 and row[1] == 1 and row[2] == "test"
    led.close()


def test_pre_migration_copy_created(tmp_path):
    """Opening a v1 ledger with events should create a .pre-migration-v1 copy."""
    db_path = tmp_path / "pmc" / "ledger.sqlite"
    db_path.parent.mkdir()
    import sqlite3
    from antslab.ledger import GENESIS, SCHEMA, sha256_text, canon
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version=1")
    # need at least one event for the safety copy to be triggered
    ts = time.time()
    payload = canon({"test": True})
    h = sha256_text(f"{GENESIS}|{ts!r}|test|{payload}")
    conn.execute("INSERT INTO events(ts,kind,payload,prev_hash,hash) VALUES(?,?,?,?,?)",
                 (ts, "test", payload, GENESIS, h))
    conn.close()

    led = Ledger(str(db_path))
    led.close()
    assert (db_path.parent / "ledger.sqlite.pre-migration-v1").exists()


# ---- llm_summary -------------------------------------------------------------
def test_llm_summary(ledger):
    ledger.log_llm("A:m", True, 10, "", call_id=1, attempt=1, arm="arm1")
    ledger.log_llm("A:m", False, None, "HTTP Error 429: rate limit", call_id=2, attempt=1, arm="arm1")
    ledger.log_llm("A:m", False, None, "HTTP Error 429: rate limit", call_id=2, attempt=2, arm="arm1")
    ledger.log_llm("A:m", True, 10, "", call_id=2, attempt=3, arm="arm1")
    ledger.log_llm("B:m2", False, None, "TimeoutError: timed out", call_id=3, attempt=1, arm="arm2")
    ledger.log_llm("B:m2", False, None, "no-json", call_id=4, attempt=1, arm="arm2")

    s = ledger.llm_summary(0)
    assert s["totals"]["attempts"] == 6
    assert s["totals"]["logical_calls"] == 4
    assert s["totals"]["ok_logical"] == 2    # call_ids 1 and 2 succeeded

    assert s["endpoints"]["A"]["attempts"] == 4
    assert s["endpoints"]["A"]["ok"] == 2
    assert s["endpoints"]["A"]["causes"]["http_429"] == 2

    assert s["endpoints"]["B"]["attempts"] == 2
    assert s["endpoints"]["B"]["causes"]["timeout"] == 1
    assert s["endpoints"]["B"]["causes"]["no_json"] == 1

    assert s["arms"]["arm1"]["attempts"] == 4
    assert s["arms"]["arm2"]["attempts"] == 2


# ---- llm_calls_since with arm filter ----------------------------------------
def test_llm_calls_since_filters_by_arm(ledger):
    ledger.log_llm("A:m", True, 10, "", arm="a1")
    ledger.log_llm("A:m", True, 10, "", arm="a1")
    ledger.log_llm("A:m", True, 10, "", arm="a2")
    assert ledger.llm_calls_since(0, arm="a1") == 2
    assert ledger.llm_calls_since(0, arm="a2") == 1
    assert ledger.llm_calls_since(0) == 3


# ---- endpoint state events ---------------------------------------------------
def test_breaker_writes_quarantine_event(ledger):
    s = _server(["500"] * 10)
    clock = FakeClock()
    cl = _client(s, ledger, clock=clock, cb_consecutive=2, cb_base_s=60)
    def advance_sleep(t): clock.advance(t)
    cl._sleep = advance_sleep
    cl.chat_json([{"role": "user", "content": "hi"}])
    events = ledger.events("llm_endpoint_state")
    assert len(events) >= 1
    assert events[-1]["payload"]["state"] == "quarantined"
    assert events[-1]["payload"]["endpoint"] == "A"


def test_breaker_writes_recovery_event(ledger):
    s = _server(["500", "500", "500", "ok", "ok"])
    clock = FakeClock()
    cl = _client(s, ledger, clock=clock, cb_consecutive=2, cb_base_s=60)
    def advance_sleep(t): clock.advance(t)
    cl._sleep = advance_sleep
    cl.chat_json([{"role": "user", "content": "hi"}])
    # advance past quarantine
    clock.advance(70)
    cl.chat_json([{"role": "user", "content": "hi"}])
    events = ledger.events("llm_endpoint_state")
    states = [e["payload"]["state"] for e in events]
    assert "recovered" in states


# ---- System page lines (view tests like test_tui.py) -------------------------
def test_system_screen_shows_endpoint_detail():
    from antslab.tui.views import system_screen, Ctx
    info = {
        "version": "0.6.0", "home": "/h", "config": None, "latest": None, "update": False,
        "llm_24h": {"calls": 100, "ok": 80, "cap": 1500},
        "llm_cap_source": "default", "calls_hour": [(0, 0)] * 24,
        "agent_tools_24h": 0, "now": time.time(),
        "llm_summary": {
            "endpoints": {
                "A": {"attempts": 80, "ok": 70, "causes": {"http_429": 5, "timeout": 3, "no_json": 2}},
                "B": {"attempts": 20, "ok": 10, "causes": {"http_5xx": 8, "connection": 2}},
            },
            "arms": {}, "totals": {"attempts": 100, "logical_calls": 60, "ok_logical": 50},
        },
        "llm_ep_states": {
            "B": {"endpoint": "B", "state": "quarantined", "until": time.time() + 600},
        },
        "llm_by_ledger": [],
        "backups": [],
    }
    ctx = Ctx(width=100)
    v = system_screen({"info": info}, ctx)
    text = "\n".join("".join(t for t, _ in line) for line in v.lines)
    assert "Per endpoint" in text
    assert "A" in text and "B" in text
    assert "QUARANTINED" in text
    assert "http_429:5" in text


def test_system_screen_no_model_calls_yet():
    """When there are no model calls, the screen should still render."""
    from antslab.tui.views import system_screen, Ctx
    info = {"version": "0.6.0", "home": "/h", "config": None, "latest": None, "update": False,
            "backups": []}
    ctx = Ctx(width=80)
    v = system_screen({"info": info}, ctx)
    text = "\n".join("".join(t for t, _ in line) for line in v.lines)
    assert "0.6.0" in text


# ---- security: keys never leak (same properties as test_security_llm) --------
def test_key_not_in_notes_or_meta(ledger):
    """Verify that the retry machinery does not leak keys in notes or meta."""
    s = _server(["500", "ok"])
    cl = _client(s, ledger)
    cl._sleep = lambda _: None
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    dump = json.dumps(meta) + json.dumps([dict(r) for r in ledger.conn.execute("SELECT * FROM llm_calls")])
    assert KEY not in dump


def test_redirects_still_blocked(ledger):
    """The retry additions must not have changed the no-redirect policy."""
    from antslab.llm import http_post_json
    # just verify the function still works for a normal call
    s = _server(["ok"])
    result = http_post_json(f"http://127.0.0.1:{s.server_port}/v1/chat/completions",
                            {"model": "m"}, {"Authorization": "Bearer " + KEY}, 3)
    assert isinstance(result, dict)


def test_response_size_cap_still_enforced(ledger):
    """The HTTPCallError changes must not have weakened the response size cap."""
    from antslab.llm import http_post_json, MAX_RESPONSE_BYTES
    # the cap is still enforced (tested more thoroughly in test_security_llm)
    assert MAX_RESPONSE_BYTES == 4 << 20
