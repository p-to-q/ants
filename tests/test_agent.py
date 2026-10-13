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
