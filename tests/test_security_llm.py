"""Hostile LLM / HTTP responses: nothing from the wire may crash, stall, flood or leak. Plain `random`, fixed seeds, no new dependency."""
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from antslab.contract import SpecError
from antslab import llm as L
from antslab.llm import Endpoint, LLMClient, extract_json, load_endpoints

KEY = "sk-" + "live-0123456789abcdefSECRET"       # assembled at run time: the public-package scanner must not trip over our own fakes


# --------------------------------------------------------------------------- extract_json
def test_deeply_nested_json_is_none_not_recursionerror():
    t = time.time()
    assert extract_json('{"a":' * 200_000 + "1" + "}" * 200_000) is None
    assert extract_json('{"a":' + "[" * 100_000 + "]" * 100_000 + "}") is None
    assert extract_json('{"a":' + "[" * 40 + "]" * 40 + "}") is None            # parseable but deeper than any legitimate reply
    assert extract_json('{"a":' + "[" * 5 + "]" * 5 + "}") == {"a": [[[[[]]]]]}
    assert time.time() - t < 5


@pytest.mark.parametrize("text", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}', '{"a": [1, 2, NaN]}',
                                  '{"a": 1, "a": 2}', '{"p": {"x": 1, "x": 2}}', '{"a": 1e999999}'])
def test_non_finite_numbers_and_duplicate_keys_are_refused(text):
    assert extract_json("prose " + text + " more prose") is None


def test_huge_and_adversarial_text_is_bounded_in_time_and_memory():
    t = time.time()
    assert extract_json("{" * 50_000_000) is None
    assert extract_json('{"a":1,' * 200_000) is None                             # every '{' would otherwise cost a full parse
    assert extract_json("x" * 10_000_000 + '{"late": 1}') is None                # beyond the amount of text we are willing to search
    assert extract_json('{"a": ' + "9" * 100_000 + "}") is None                  # int digits limit
    assert time.time() - t < 10


def test_non_text_input_is_none():
    for v in (None, 1, 2.5, b'{"a":1}', ["{}"], {"a": 1}, object()):
        assert extract_json(v) is None


def test_values_with_control_characters_survive_as_data():
    # extract_json does not rewrite data; printing is where control characters are neutralised (see test_security_ui)
    assert extract_json('{"r": "\\u001b[2J\\r"}') == {"r": "\x1b[2J\r"}


def _random_json_like(rnd: random.Random) -> str:
    toks = ['{', '}', '[', ']', ':', ',', '"a"', '"proposals"', '"rationale"', '1', '-0', '1e999', '1.5e-3', 'NaN', 'Infinity', 'null', 'true',
            'false', '"\\ud800"', '"\\u0000"', '"\x1b[31m"', '```json', '```', 'x', ' ', '\n', '\x00', '\ud800', '日本', '"' + 'z' * 50 + '"']
    base = ''.join(rnd.choice(toks) for _ in range(rnd.randint(0, 60)))
    if rnd.random() < 0.3:
        good = json.dumps({"proposals": [{"a": rnd.random(), "b": rnd.randint(-5, 50), "c": rnd.choice("uvw")}], "rationale": "r"})
        i = rnd.randint(0, len(good))
        good = good[:i] + rnd.choice(toks) + good[i:]
        base = base + good
    return base


def test_extract_json_fuzz_20k_never_raises_and_only_returns_dict_or_none():
    rnd = random.Random(20240601)
    for _ in range(20_000):
        r = extract_json(_random_json_like(rnd))
        assert r is None or (isinstance(r, dict) and all(isinstance(k, str) for k in r))


# --------------------------------------------------------------------------- base URL policy
@pytest.mark.parametrize("base", ["file:///etc", "ftp://x/y", "http://example.com/v1", "http://192.0.2.1:8080", "https://user:pw@h/v1",
                                  "https://h/v1?key=abc", "https://h/v1#frag", "https:///v1", "", "ws://h", "//h/v1", "h/v1"])
def test_unsafe_endpoint_bases_are_refused(base):
    with pytest.raises(ValueError):
        L.check_base(base)


@pytest.mark.parametrize("base", ["https://api.example.com/v1", "http://127.0.0.1:8080/v1", "http://localhost/v1", "http://[::1]:9/v1"])
def test_safe_endpoint_bases_are_accepted(base):
    assert L.check_base(base) == base


def test_a_refused_base_is_a_failed_call_not_a_request(ledger):
    cl = LLMClient([Endpoint("A", "http://203.0.113.9:9/v1", KEY, "m")], ledger, timeout_s=5)
    t = time.time()
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None and "endpoint base" in meta["attempts"][0]["note"] and time.time() - t < 2


# --------------------------------------------------------------------------- hostile servers (real sockets on loopback)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.server.auth.append(self.headers.get("Authorization"))
        getattr(self, "mode_" + self.server.mode)()

    def mode_flood(self):                                   # a valid-looking JSON document that never ends (64 MB)
        self.send_response(200)
        self.end_headers()
        sent = 0
        try:
            self.wfile.write(b'{"choices":[{"message":{"content":"')
            chunk = b"a" * (1 << 20)
            for _ in range(64):
                self.wfile.write(chunk)
                sent += len(chunk)
            self.wfile.write(b'"}}]}')
        except OSError:
            pass
        self.server.sent = sent

    def mode_drip(self):                                    # 1 byte every 0.25 s: each read is "fast", the total is not
        body = b'{"choices":[{"message":{"content":"{}"}}]}' + b" " * 40
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for b in body:
                self.wfile.write(bytes([b]))
                self.wfile.flush()
                time.sleep(0.25)
        except OSError:
            pass

    def mode_redirect(self):
        self.send_response(302)                             # urllib follows 301/302/303 for POST (as GET) and re-sends Authorization
        self.send_header("Location", self.server.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):                                       # the redirect target of a followed POST arrives as a GET
        self.server.auth.append(self.headers.get("Authorization"))
        self.mode_ok()

    def mode_ok(self):
        data = self.server.reply
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def server(mode, **attrs):
    s = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    s.mode, s.auth, s.sent = mode, [], 0
    for k, v in attrs.items():
        setattr(s, k, v)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def client(s, ledger, key=KEY, timeout=5):
    return LLMClient([Endpoint("A", f"http://127.0.0.1:{s.server_port}/v1", key, "m")], ledger, timeout_s=timeout)


def reply(content='{"proposals": []}', **extra):
    return json.dumps({"choices": [{"message": {"content": content}}], **extra}).encode()


def test_a_response_flood_is_cut_off_at_the_size_cap(ledger):
    s = server("flood")
    t = time.time()
    obj, meta = client(s, ledger, timeout=20).chat_json([{"role": "user", "content": "hi"}])
    assert obj is None and "too large" in meta["attempts"][0]["note"]
    assert time.time() - t < 10
    time.sleep(0.5)
    assert s.sent < 40 << 20, f"the client kept reading: server pushed {s.sent >> 20} MB"


def test_a_slow_drip_response_hits_an_overall_deadline(ledger):
    s = server("drip")
    t = time.time()
    obj, meta = client(s, ledger, timeout=1).chat_json([{"role": "user", "content": "hi"}])
    assert obj is None and time.time() - t < 4
    assert "slow" in meta["attempts"][0]["note"] or "Timeout" in meta["attempts"][0]["note"]


def test_redirects_are_never_followed_so_the_key_cannot_be_forwarded(ledger):
    thief = server("ok", reply=reply())
    s = server("redirect", target=f"http://127.0.0.1:{thief.server_port}/steal")
    obj, meta = client(s, ledger).chat_json([{"role": "user", "content": "hi"}])
    assert obj is None and thief.auth == [], "Authorization header was forwarded to the redirect target"
    assert s.auth == ["Bearer " + KEY]
    assert KEY not in json.dumps(meta)


@pytest.mark.parametrize("usage", [{"completion_tokens": {"x": 1}}, {"completion_tokens": 10 ** 30}, {"completion_tokens": "abc"},
                                   {"completion_tokens": True}, {"completion_tokens": -5}, {"completion_tokens": 1.5}, "junk", [1], None])
def test_hostile_usage_fields_cannot_break_the_audit_row(ledger, usage):
    s = server("ok", reply=reply(usage=usage))
    obj, meta = client(s, ledger).chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []}
    assert ledger.conn.execute("SELECT tokens FROM llm_calls").fetchone()[0] is None


@pytest.mark.parametrize("body", [b"[]", b'{"choices": []}', b'{"choices": [1]}', b'{"choices": "x"}', b'{"choices":[{"message":null}]}',
                                  b'{"choices":[{"message":{"content":["{}"]}}]}', b'{"choices":[{"message":{"content":null}}]}',
                                  b'{"choices":[{"message":{"content":"{}"}}], "usage": {"completion_tokens": NaN}}',
                                  b'{"choices":[{"message":{"content":"{}"}}], "x": 1, "x": 2}', b"\xff\xfe\x00", b"", b"null",
                                  b'{"choices":[{"message":{"content":"{}"}}]} trailing', b"{" * 100_000])
def test_malformed_response_bodies_are_failed_calls(ledger, body):
    s = server("ok", reply=body)
    obj, meta = client(s, ledger).chat_json([{"role": "user", "content": "hi"}])
    assert obj is None or obj == {}
    assert meta["attempts"] and isinstance(meta["attempts"][0]["note"], str)


def test_a_failing_ledger_does_not_take_the_proposer_down():
    class Broken:
        def log_llm(self, *a, **k):
            raise RuntimeError("disk full")
    s = server("ok", reply=reply())
    cl = LLMClient([Endpoint("A", f"http://127.0.0.1:{s.server_port}/v1", KEY, "m")], Broken(), timeout_s=5)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj == {"proposals": []} and meta["ep"] == "A"


# --------------------------------------------------------------------------- secrets in notes / ledger / meta
def _boom(text):
    class C(LLMClient):
        def _post(self, ep, body):
            raise RuntimeError(text)
    return C


def test_a_key_cut_in_half_by_truncation_is_still_redacted(ledger):
    pad = "x" * (160 - len("RuntimeError: ") - 6)           # the truncation boundary falls 6 characters into the key
    cl = _boom(pad + KEY + " tail")([Endpoint("A", "https://h/v1", KEY, "m")], ledger)
    _, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    note = meta["attempts"][0]["note"]
    assert KEY[:5] not in note and KEY[-5:] not in note
    rows = json.dumps([dict(r) for r in ledger.conn.execute("SELECT * FROM llm_calls")])
    assert KEY[:5] not in rows


def test_an_empty_key_does_not_shred_the_error_text(ledger):
    cl = _boom("connection reset")([Endpoint("A", "https://h/v1", "", "m")], ledger)
    _, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert meta["attempts"][0]["note"] == "RuntimeError: connection reset"


def test_error_text_from_a_hostile_server_cannot_carry_terminal_escapes(ledger):
    cl = _boom("\x1b[2J\x1b]0;pwned\x07 fake\rline‮ rtl")([Endpoint("A", "https://h/v1", KEY, "m")], ledger)
    _, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    stored = ledger.conn.execute("SELECT note FROM llm_calls").fetchone()[0]
    for text in (meta["attempts"][0]["note"], stored):
        assert not any(ord(c) < 32 or 127 <= ord(c) < 160 or c in "‮" for c in text), repr(text)


def test_endpoint_repr_and_env_errors_never_contain_the_key(tmp_path):
    assert KEY not in repr(Endpoint("A", "https://h", KEY, "m")) and KEY not in str(Endpoint("A", "https://h", KEY, "m"))
    p = tmp_path / "llm.env"
    p.write_text(f"LLM_A_BASE=https://h\nLLM_A_KEY={KEY}\n")
    os.chmod(p, 0o640)
    with pytest.raises(PermissionError) as e:
        load_endpoints(p)
    assert KEY not in str(e.value)
    os.chmod(p, 0o600)
    with pytest.raises(ValueError) as e:
        load_endpoints(p)                                     # model id missing
    assert KEY not in str(e.value)


def test_the_supervisor_gives_evaluation_children_a_clean_environment(tmp_path, monkeypatch):
    from antslab.runner import run_command
    for k in ("LLM_A_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "sk-" + "live-should-not-leak")
    script = "import json, os; json.dump({'x': 1}, open('metrics.json', 'w')); json.dump(sorted(os.environ), open('env.json', 'w'))"
    r = run_command([sys.executable, "-c", script], tmp_path / "o", 30, ["x"])
    assert r.metrics, r.error                      # metrics.json must stay a flat dict of numbers; the environment goes to a side file
    seen = json.loads((tmp_path / "o" / "env.json").read_text())
    leaked = [k for k in seen if ("KEY" in k or "TOKEN" in k or "SECRET" in k) and k != "ANTS_TRIAL_TOKEN"]     # the runner's own per-trial marker is not a secret
    assert leaked == []


# --------------------------------------------------------------------------- judge client shares the same transport rules
def test_jev_client_does_not_follow_redirects_with_its_key(tmp_path):
    from antslab.judge import JevClient
    thief = server("ok", reply=b"{}")
    s = server("redirect", target=f"http://127.0.0.1:{thief.server_port}/steal")
    cl = JevClient(base_url=f"http://127.0.0.1:{s.server_port}/v1", key=KEY, timeout_s=5)
    with pytest.raises(Exception):
        cl.ask({"x": 1}, {"q": {"type": "noul"}})
    assert thief.auth == []


def test_jev_probabilities_must_be_finite_numbers_in_unit_interval():
    from antslab.judge import JevClient
    for bad in ({"true": float("nan"), "false": 0.5}, {"true": float("inf"), "false": 0.0}, {"true": 5, "false": -4}, {"true": "0.9", "false": "0.1"},
                {"true": [1], "false": 0}, {"true": 1e400, "false": 0}):
        ans, _ = JevClient(post=lambda b, bad=bad: {"answers": {"q": {"choice": "true", "probabilities": bad}}}).ask({}, {"q": {}})
        assert ans["q"]["probs"] is None, bad
    ans, _ = JevClient(post=lambda b: {"answers": {"q": {"choice": {"x": 1}, "confidence": float("nan"), "score": "x"}}}).ask({}, {"q": {}})
    assert ans["q"]["choice"] is None and ans["q"]["confidence"] is None and ans["q"]["score"] is None


def test_calibration_ignores_out_of_range_probabilities(ledger):
    from antslab import judge as J
    for i, p in enumerate([0.9, 0.1, 5.0, -3.0, float("inf")]):
        jid = ledger.log_judgement("j", "k", f"s{i}", p, "yes", "{}", "shadow")
        ledger.resolve_judgement(jid, i % 2 == 0)
    assert J.calibration(ledger, "j", "k")["n"] == 2


def test_env_file_values_may_be_quoted_and_exported(tmp_path):
    p = tmp_path / "llm.env"
    p.write_text(f'# comment\nexport LLM_A_BASE="https://h/v1"\nLLM_A_KEY=\'{KEY}\'\n LLM_A_MODEL = m1 \n')
    os.chmod(p, 0o600)
    (ep,) = load_endpoints(p)
    assert (ep.base, ep.key, ep.model) == ("https://h/v1", KEY, "m1")
