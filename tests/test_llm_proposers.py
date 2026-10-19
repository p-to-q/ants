import json, os, random, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from antslab.contract import SpecError, spec_key
from antslab.dream import dream, latest_memory
from antslab.llm import LLMClient, load_endpoints, extract_json, Endpoint
from antslab.proposers import EvoProposer, LLMProposer, RandomProposer, TPEProposer, make_proposer


def test_space_validation_is_strict(make_contract):
    sp = make_contract().space
    good = {"a": 0.5, "b": 7, "c": "u"}
    assert sp.validate(good) == good
    for bad in ({**good, "d": 1}, {"a": 0.5, "b": 7}, {**good, "a": 1.5}, {**good, "b": 7.5},
                {**good, "c": "z"}, {**good, "a": float("nan")}, {**good, "b": True}, {**good, "a": "0.5"}):
        with pytest.raises(SpecError):
            sp.validate(bad)


def test_extract_json_handles_fences_and_prose():
    assert extract_json('thinking...\n```json\n{"a": {"b": 1}}\n```\nbye') == {"a": {"b": 1}}
    assert extract_json("no json here") is None
    assert extract_json('{broken {"ok": 1}') == {"ok": 1}


class FakeClient:
    def __init__(self, replies):
        self.replies, self.i = replies, 0

    def chat_json(self, messages, temperature=0.9):
        r = self.replies[self.i % len(self.replies)]; self.i += 1
        return (r, {"ep": "A"}) if r is not None else (None, {"attempts": []})


def _drain(p, ledger, k=20):
    for _ in range(50):
        time.sleep(0.05)
        with p.lock:
            if p.inflight == 0:
                break
    return p.propose(ledger, "arm", k, set())


def test_llm_proposals_are_validated_and_deduplicated(ledger, make_contract):
    c = make_contract()
    ok1, ok2 = {"a": 0.1, "b": 3, "c": "v"}, {"a": 0.9, "b": 25, "c": "w"}
    reply = {"proposals": [ok1, dict(ok1), {"a": 0.1001, "b": 3, "c": "v"},      # exact + near duplicate
                           {"a": 9, "b": 3, "c": "v"}, {"a": 0.5, "b": 3, "c": "v", "x": 1},   # out of range, extra key
                           "import os", ok2], "rationale": "r"}
    p = LLMProposer(c, FakeClient([reply]), EvoProposer(c, random.Random(0)), random.Random(0), per_call=6)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger, 2)
    llm = [x for x in out if x.source.startswith("llm")]
    assert sorted(spec_key(x.spec) for x in llm) == sorted([spec_key(ok1), spec_key(ok2)])
    assert p.stats["invalid"] == 3 and p.stats["duplicate"] >= 2 and p.stats["accepted"] == 2


def test_llm_failure_falls_back_to_evo_without_idling(ledger, make_contract):
    c = make_contract()
    p = LLMProposer(c, FakeClient([None]), EvoProposer(c, random.Random(0)), random.Random(0))
    out = _drain(p, ledger, 4)
    assert len(out) == 4 and all(x.source.startswith("evo") for x in out)
    assert p.stats["failed_calls"] >= 1


def test_arms_are_reproducible_for_same_seed(ledger, make_contract):
    c = make_contract()
    for arm in ("random", "evo"):
        a = make_proposer(arm, c, 7).propose(ledger, "x", 8, set())
        b = make_proposer(arm, c, 7).propose(ledger, "x", 8, set())
        assert [spec_key(x.spec) for x in a] == [spec_key(x.spec) for x in b]


def test_tpe_proposer_runs_and_respects_space(ledger, make_contract):
    pytest.importorskip("optuna")
    c = make_contract()
    rnd = random.Random(1)
    for i in range(14):
        s = c.space.sample(rnd)
        tid = ledger.enqueue("toy", "t", i, s, "x"); t = ledger.claim(60, arm="t")
        ledger.finish(t["id"], 1, "done", {"val_mse": 1.0 + (s["a"] - .3) ** 2, "n_params": 50})
    out = make_proposer("tpe", c, 3).propose(ledger, "t", 5, set())
    assert len(out) == 5 and all(c.space.validate(o.spec) for o in out)


def test_dream_drops_claims_not_supported_by_statistics(ledger, make_contract):
    c = make_contract()
    for i in range(30):
        cc = "u" if i % 2 else "w"
        s = {"a": 0.3, "b": 7, "c": cc}
        ledger.enqueue("toy", "d", i, s, "x"); t = ledger.claim(60, arm="d")
        ledger.finish(t["id"], 1, "done", {"val_mse": 0.5 if cc == "u" else 1.0, "n_params": 70})
    lie = {"lessons": [{"param": "c", "value": "w", "claim": "better", "note": "planted"},
                       {"param": "c", "value": "u", "claim": "better", "note": "true"},
                       {"param": "zzz", "value": "1", "claim": "better", "note": "invented"}]}
    lines = dream(ledger, c, "d", FakeClient([lie]))
    text = "\n".join(lines)
    assert "planted" not in text and "invented" not in text and "true" in text
    ev = ledger.events("dream")[-1]["payload"]
    assert ev["unsupported_dropped"] == 2 and "c=u" in latest_memory(ledger, "d")


# ---- real HTTP path against a fake server ---------------------------------
class H(BaseHTTPRequestHandler):
    mode = "ok"
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.keys.append(self.headers.get("Authorization"))
        if self.server.mode == "500":
            self.send_response(500); self.end_headers(); return
        body = {"choices": [{"message": {"content": 'thinking\n```json\n{"proposals": [], "rationale": "x"}\n```',
                             "reasoning_content": "..."}}], "usage": {"completion_tokens": 42}}
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): pass


def _server(mode):
    s = HTTPServer(("127.0.0.1", 0), H); s.mode, s.keys = mode, []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def test_client_fails_over_and_logs_without_leaking_keys(ledger):
    bad, good = _server("500"), _server("ok")
    eps = [Endpoint("A", f"http://127.0.0.1:{bad.server_port}", "SECRET-A", "m"),
           Endpoint("B", f"http://127.0.0.1:{good.server_port}", "SECRET-B", "m")]
    cl = LLMClient(eps, ledger, timeout_s=5, rng=random.Random(0))
    for _ in range(6):
        obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
        assert obj == {"proposals": [], "rationale": "x"} and meta["ep"] == "B"
    assert cl.stats()["B"]["ok"] == 6
    assert ledger.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] >= 6
    dump = json.dumps([dict(r) for r in ledger.conn.execute("SELECT * FROM llm_calls")]) + \
           json.dumps(ledger.events())
    assert "SECRET" not in dump                                      # keys never reach the ledger


def test_env_file_permissions_are_enforced(tmp_path):
    p = tmp_path / "llm.env"
    p.write_text("LLM_A_BASE=http://x\nLLM_A_KEY=k\n")
    os.chmod(p, 0o644)
    with pytest.raises(PermissionError):
        load_endpoints(p)
    os.chmod(p, 0o600)
    with pytest.raises(ValueError, match="LLM_A_MODEL"):
        load_endpoints(p)                                              # no model id is hard-coded any more
    p.write_text("LLM_A_BASE=http://x\nLLM_A_KEY=k\nLLM_A_MODEL=m1\nLLM_B_BASE=http://y\nLLM_B_KEY=k2\nLLM_B_MODEL=m2\n")
    assert [(e.name, e.model) for e in load_endpoints(p)] == [("A", "m1"), ("B", "m2")]


def test_generated_values_never_leave_declared_bounds(make_contract):
    """Regression: exp(log(hi)) can exceed hi by 1 ulp and crash a long run hours in."""
    from antslab.contract import Param
    rnd = random.Random(0)
    p = Param("x", "logfloat", 0.001, 10.0)
    for _ in range(20000):
        for v in (p.sample(rnd), p.neighbor(10.0, rnd, 0.5), p.neighbor(0.001, rnd, 0.5), p.from_unit(rnd.uniform(-1, 2))):
            assert 0.001 <= v <= 10.0
            p.validate(v)
    assert p.from_unit(1.0) == 10.0 and p.from_unit(0.0) == 0.001


def test_operator_success_means_archive_improvement(ledger, make_contract):
    """Regression: success must be judged before the cell archive is updated, and only improvements count."""
    from antslab.history import History
    c = make_contract()
    h = History(ledger, c, "h")
    vals = [("random", 5.0), ("evo", 4.0), ("evo", 4.5), ("qd", 3.0), ("evo", 3.0)]
    for i, (src, v) in enumerate(vals):
        ledger.enqueue("toy", "h", i, {"a": 0.5, "b": 5, "c": "u"}, src, spec_key=f"k{i}")
        t = ledger.claim(60, arm="h")
        ledger.finish(t["id"], 1, "done", {"val_mse": v, "n_params": 60})
    h.update()
    assert h.ops["random"]["n"] == 1 and h.ops["random"]["succ"] == 1        # opens the cell
    assert h.ops["evo"]["n"] == 3 and h.ops["evo"]["succ"] == 1           # 4.0 improves; 4.5 and 3.0-tie do not
    assert h.ops["qd"]["n"] == 1 and h.ops["qd"]["succ"] == 1


def test_portfolio_survives_operator_failures_and_exhaustion(ledger, make_contract):
    """Regression: allocation must not assume every operator is still available (KeyError in a long run)."""
    from antslab.portfolio import PortfolioProposer, build_portfolio
    c = make_contract()
    pf = build_portfolio(c, 1, with_tpe=False)
    class Boom:
        def propose(self, *a, **k): raise RuntimeError("bug in operator")
    pf.ops["evo"] = Boom()
    seen = set()
    for _ in range(30):
        out = pf.propose(ledger, "p", 4, seen)
        assert len(out) == 4 and all(c.space.validate(p.spec) for p in out)
    assert pf.stats["errors"]["evo"] >= 1 and "bug in operator" in pf.stats["last_error"]


def test_over_cap_records_are_stepping_stones_not_leaders(ledger, make_contract):
    """Hard cap = constraint: infeasible records never rank, never reward operators, but stay in the QD archive."""
    from antslab.history import History
    c = make_contract()                       # caps [100, 300] on n_params
    h = History(ledger, c, "h")
    rows = [({"a": .1, "b": 5, "c": "u"}, 1.0, 50), ({"a": .2, "b": 5, "c": "v"}, 0.1, 5000)]   # 2nd: best value, but over cap
    for i, (sp_, v, n) in enumerate(rows):
        ledger.enqueue("toy", "h", i, sp_, "evo", spec_key=f"k{i}")
        t = ledger.claim(60, arm="h")
        ledger.finish(t["id"], 1, "done", {"val_mse": v, "n_params": n})
    h.update()
    assert [r["key"] for r in h.ranked()] == ["k0"]            # the over-cap model is not a leader
    assert h.best_s0 == 1.0 and h.ops["evo"]["n"] == 2 and h.ops["evo"]["succ"] == 1
    assert any(rec["key"] == "k1" for rec in h.cells.values())  # but it is archived (stepping stone)


def test_cells_cap_bin_isolates_feasible_from_infeasible(ledger, make_contract):
    """cap_bin maps feasible records to a finite cap and infeasible records to inf,
    so they never share a MAP-Elites cell. An infeasible record cannot displace a
    feasible elite even with a better signed value — but it stays in its own cell
    as a stepping stone for QD. This invariant is structural (cap_bin, not a flag
    check) and must survive any future refactoring of the cell key."""
    from antslab.history import History
    c = make_contract()                       # caps [100, 300] on n_params
    h = History(ledger, c, "h")
    # Same spec skeleton (a=.1, b=5, c="u") → same niche. Only n_params differs.
    # Feasible: n_params=50 → cap_bin=100, val_mse=1.0
    ledger.enqueue("toy", "h", 0, {"a": .1, "b": 5, "c": "u"}, "evo", spec_key="f0")
    t = ledger.claim(60, arm="h")
    ledger.finish(t["id"], 1, "done", {"val_mse": 1.0, "n_params": 50})
    # Infeasible: n_params=5000 → cap_bin=inf, val_mse=0.1 (better, but over cap)
    ledger.enqueue("toy", "h", 1, {"a": .1, "b": 5, "c": "u"}, "evo", spec_key="i1")
    t = ledger.claim(60, arm="h")
    ledger.finish(t["id"], 1, "done", {"val_mse": 0.1, "n_params": 5000})
    h.update()
    feasible_cells = {cell for cell, rec in h.cells.items() if rec["key"] == "f0"}
    infeasible_cells = {cell for cell, rec in h.cells.items() if rec["key"] == "i1"}
    assert feasible_cells and infeasible_cells               # both archived
    assert feasible_cells.isdisjoint(infeasible_cells)       # different cap_bin → different cell
    for cell, rec in h.cells.items():
        if rec["key"] == "f0":
            assert rec["feasible"] is True                   # feasible elite not displaced


def test_tls_context_verifies_and_failures_are_visible(ledger):
    """Regression: python.org builds have no CA bundle -> every HTTPS call failed silently. Never disable verification."""
    import ssl
    from antslab.llm import ssl_context
    ctx = ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname and ctx.cert_store_stats()["x509_ca"] > 0
    ep = Endpoint("A", "https://127.0.0.1:1", "SECRETKEY123", "m")           # refused connection: reason must be recorded, key redacted
    cl = LLMClient([ep], ledger, timeout_s=3)
    obj, meta = cl.chat_json([{"role": "user", "content": "hi"}])
    assert obj is None and meta["attempts"][0]["note"] and "SECRETKEY123" not in json.dumps(meta)
    assert "SECRETKEY123" not in json.dumps([dict(r) for r in ledger.conn.execute("SELECT * FROM llm_calls")])
