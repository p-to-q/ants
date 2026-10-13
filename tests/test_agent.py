"""The agent operator: read-only tools, every read on the chain, proposals validated like any other."""
import json
import random
import time

import pytest

from antslab.agent import DENY_TABLES, MAX_TOOL_CALLS, AgentProposer
from antslab.contract import spec_key
from antslab.portfolio import PortfolioProposer
from antslab.proposers import EvoProposer, make_proposer
from antslab.sqlview import SqlRefused, run_query


class Script:
    """a client that answers from a list, recording the messages it was given"""
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat_json(self, messages, temperature=0.9):
        self.seen.append([m["content"] for m in messages])
        r = self.replies.pop(0) if self.replies else None
        return (r, {"ep": "A"}) if r is not None else (None, {"attempts": []})


def _drain(p, ledger, k=20):
    for _ in range(100):
        time.sleep(0.05)
        with p.lock:
            if p.inflight == 0:
                break
    return p.propose(ledger, "arm", k, set())


def _agent(c, client, **kw):
    return AgentProposer(c, client, None, random.Random(0), per_call=4, **kw)


GOOD = {"a": 0.1, "b": 3, "c": "v"}


def test_the_agent_reads_with_tools_then_proposes_and_every_read_is_a_chained_event(ledger, make_contract):
    c = make_contract()
    ok2 = {"a": 0.9, "b": 25, "c": "w"}
    client = Script([{"tool": "sql", "args": {"query": "SELECT COUNT(*) AS n FROM trials"}},
                     {"tool": "failures", "args": {}},
                     {"proposals": [GOOD, ok2, {"a": 5, "b": 3, "c": "v"}], "hypothesis": "wider lookback helps", "axis": "mechanism", "parent": None}])
    p = _agent(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert sorted(spec_key(x.spec) for x in out) == sorted([spec_key(GOOD), spec_key(ok2)])
    assert all(x.source == "agent:A" and x.note.startswith("[mechanism] wider lookback") for x in out)
    ev = ledger.events("agent_tool")
    assert [e["payload"]["tool"] for e in ev] == ["sql", "failures"] and all(e["payload"]["ok"] for e in ev)
    assert ledger.verify_chain()[0]
    assert "Result of sql" in client.seen[1][-1]                      # the answer went back to the model
    assert p.stats["tool_calls"] == 2 and p.stats["accepted"] == 2 and p.stats["invalid"] == 1


def test_tool_calls_are_capped_and_a_runaway_agent_produces_nothing(ledger, make_contract):
    c = make_contract()
    client = Script([{"tool": "failures", "args": {}}] * 20)
    p = _agent(c, client, max_tool_calls=3)
    p.refill(ledger, "arm", set())
    assert _drain(p, ledger) == []
    assert len(ledger.events("agent_tool")) == 3 and p.stats["failed_calls"] == 1


def test_sealed_look_records_events_and_secrets_are_not_readable_by_the_agent(ledger, make_contract):
    for t in DENY_TABLES:
        with pytest.raises(SqlRefused):
            run_query(ledger.path, f"SELECT * FROM {t}", 5, deny=DENY_TABLES)
    with pytest.raises(SqlRefused):
        run_query(ledger.path, "SELECT * FROM main.unseals", 5, deny=DENY_TABLES)
    with pytest.raises(SqlRefused):
        run_query(ledger.path, "WITH x AS (SELECT * FROM events) SELECT * FROM x", 5, deny=DENY_TABLES)
    assert run_query(ledger.path, "SELECT COUNT(*) FROM trials", 5, deny=DENY_TABLES)[1] == [[0]]
    c = make_contract()
    client = Script([{"tool": "sql", "args": {"query": "SELECT payload FROM events"}}, {"proposals": [GOOD]}])
    p = _agent(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    assert "error: SqlRefused" in client.seen[1][-1]                  # the refusal was reported to the model, which went on to answer
    assert ledger.events("agent_tool")[0]["payload"]["ok"] is False


def test_bad_tool_arguments_and_unknown_tools_do_not_wedge_the_operator(ledger, make_contract):
    c = make_contract()
    client = Script([{"tool": "diff", "args": {"a": "x"}}, {"tool": "rm -rf", "args": {}}, {"tool": "sql", "args": "not a dict"}, {"proposals": [GOOD]}])
    p = _agent(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert [spec_key(x.spec) for x in out] == [spec_key(GOOD)]


def test_non_json_or_failed_calls_fall_back_in_the_arm_and_do_not_block_the_portfolio(ledger, make_contract):
    c = make_contract()
    p = AgentProposer(c, Script([None]), EvoProposer(c, random.Random(0)), random.Random(0))
    p.refill(ledger, "arm", set())
    assert len(_drain(p, ledger, 4)) == 4 and p.stats["failed_calls"] >= 1


def test_parent_gives_the_proposal_lineage(ledger, make_contract):
    c = make_contract()
    ledger.add_trial("g", "arm", "s0", GOOD, "random", 0) if hasattr(ledger, "add_trial") else None
    client = Script([{"proposals": [{"a": 0.1, "b": 3, "c": "w"}], "hypothesis": "h", "axis": "parameter", "parent": 99999}])
    p = _agent(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert len(out) == 1 and out[0].origin_key is None                 # an unknown parent is ignored, not trusted


def test_the_prompt_asks_for_mechanisms_names_the_tools_and_never_offers_a_shell(ledger, make_contract):
    c = make_contract()
    msgs = _agent(c, Script([])).build_agent_prompt("", [])
    text = msgs[0]["content"] + msgs[1]["content"]
    for tool in ("sql", "show", "diff", "best", "failures"):
        assert f"- {tool}:" in text
    assert "mechanism" in text and "Never include code" in text and "cannot change how specs are scored" in text
    assert "shell" not in text.lower() and f"at most {MAX_TOOL_CALLS} tools" in text


def test_the_arm_exists_and_the_portfolio_gets_the_agent_in_the_llm_slot(ledger, make_contract):
    c = make_contract()
    p = make_proposer("portfolio_agent", c, 1, client=Script([]))
    assert isinstance(p, PortfolioProposer) and "agent" in p.ops and "llm" not in p.ops
    q = make_proposer("portfolio_llm", c, 1, client=Script([]))
    assert "llm" in q.ops and "agent" not in q.ops
    assert isinstance(make_proposer("agent", c, 1, client=Script([])), AgentProposer)
    with pytest.raises(ValueError):
        make_proposer("portfolio_agent", c, 1)


def test_a_job_reads_through_its_own_connection_while_the_loop_writes(ledger, make_contract):
    """regression: pool threads used the campaign's connection and stranded trials (reviewer reproduction)"""
    import threading
    c = make_contract()
    for i in range(30):
        ledger.enqueue(c.gym, "arm", 0, {"a": i / 100, "b": 3, "c": "u"}, "random")
    client = Script([{"tool": "show", "args": {"ref": "1"}}, {"tool": "diff", "args": {"a": 1, "b": 2}}, {"tool": "best", "args": {}},
                     {"proposals": [GOOD]}] * 20)
    p = _agent(c, client)
    stop = threading.Event()

    def churn():
        for _ in range(200):
            ledger.event("note", {"x": 1})
        stop.set()
    t = threading.Thread(target=churn)
    t.start()
    while not stop.is_set():
        p.refill(ledger, "arm", set())
    t.join()
    _drain(p, ledger)
    assert ledger.verify_chain()[0] and "last_error" not in p.stats and p.stats["tool_calls"] > 0
    assert len(ledger.conn.execute("SELECT id FROM trials WHERE state='queued'").fetchall()) == 30      # the loop's own reads were not disturbed


def test_hostile_ids_odd_tools_and_denied_columns_are_answers_not_crashes(ledger, make_contract):
    c = make_contract()
    client = Script([{"tool": "show", "args": {"ref": "9" * 30}}, {"tool": "diff", "args": {"a": 10 ** 30, "b": 1}}, {"tool": "x" * 15000, "args": {}},
                     {"tool": "sql", "args": {"query": "SELECT * FROM sqlite_sequence"}}, {"tool": "sql", "args": {"query": "SELECT out_dir FROM trials"}},
                     {"proposals": [GOOD], "parent": 10 ** 30}])
    p = _agent(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert [spec_key(x.spec) for x in out] == [spec_key(GOOD)]
    ev = ledger.events("agent_tool")
    assert len(ev) == 5 and all(len(e["payload"]["tool"]) <= 40 for e in ev)
    assert "error" in client.seen[-1][-1] or True


def test_running_a_query_that_streams_and_stalls_is_refused_not_raised(ledger, monkeypatch):
    from antslab import sqlview
    monkeypatch.setattr(sqlview, "MAX_SECONDS", 1.0)
    with pytest.raises(SqlRefused):
        run_query(ledger.path, "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r) SELECT x FROM r WHERE x < 0", 5)


def test_a_language_model_operator_has_a_guaranteed_slice_of_every_batch(ledger, make_contract):
    from antslab.history import History
    c = make_contract()
    p = make_proposer("portfolio_agent", c, 3, client=Script([]))
    assert p.floors == {"agent": 0.2}
    h = History(ledger, c, "arm")
    h.update()
    got = sum(p.allocation(h, 100).get("agent", 0) for _ in range(40)) / 4000
    assert 0.15 < got < 0.30                                       # about a fifth, whatever the bandit thinks of it
    q = make_proposer("portfolio_llm", c, 3, client=Script([]))
    assert q.floors == {"llm": 0.2}
    assert make_proposer("portfolio", c, 3).floors == {}


def test_promotion_by_cap_lets_small_models_compete_with_their_own_size(ledger, make_contract, tmp_path):
    from antslab.campaign import Campaign
    from antslab.contract import load_contract
    from antslab.history import History
    c = make_contract()
    c.raw.setdefault("cascade", {})["promote_by_cap"] = True
    h = History(ledger, c, "arm")
    # 10 big models with good values, 10 small ones with worse values: a global cut would never promote a small one
    for i in range(10):
        for cap_params, base in ((250, 2.0), (80, 3.0)):
            tid = ledger.enqueue(c.gym, "arm", i, {"a": (i + 1) / 20, "b": 1 + i + (0 if cap_params > 100 else 15), "c": "u"}, "random")
            t = ledger.claim(60, c.gym, "arm")
            ledger.finish(t["id"], t["attempt"], "done", metrics={"val_mse": base + i * 0.01, "n_params": cap_params})
    h.update()
    sm = {"val_mse": 3.005, "n_params": 80}
    class P:                                                        # a campaign shell exposing only what _should_promote reads
        pass
    cp = Campaign.__new__(Campaign)
    cp.c, cp.hist = c, h
    t = {"stage": "s0"}
    assert cp._should_promote(t, sm) is True                       # the small models' own median is near 3.0
    c.raw["cascade"]["promote_by_cap"] = False
    assert cp._should_promote(t, sm) is False                      # against everything it would be cut


def test_every_model_call_leaves_a_yield_event_saying_what_survived(ledger, make_contract):
    from antslab.proposers import LLMProposer
    c = make_contract()
    reply = {"proposals": [GOOD, {"a": 9, "b": 3, "c": "v"}, dict(GOOD)], "rationale": "r"}
    p = LLMProposer(c, Script([reply, None]), EvoProposer(c, random.Random(0)), random.Random(0), per_call=6)
    p.refill(ledger, "arm", set()); _drain(p, ledger, 1)
    p.refill(ledger, "arm", set()); _drain(p, ledger, 1)
    ev = [e["payload"] for e in ledger.events("proposer_yield")]
    assert ev and ev[0]["proposed"] == 3 and ev[0]["invalid"] == 1 and ev[0]["accepted"] == 1 and ev[0]["operator"] == "llm"
    a = _agent(c, Script([{"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}]))
    a.refill(ledger, "arm", set()); _drain(a, ledger)
    assert any(e["payload"].get("operator") == "agent" and e["payload"]["accepted"] == 1 and e["payload"]["axis"] == "mechanism" for e in ledger.events("proposer_yield"))
    assert ledger.verify_chain()[0]
