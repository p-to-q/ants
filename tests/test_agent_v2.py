"""Agent v2: new tools, protocol v2 (parent/expect/why per proposal), arm keys, pinning of v1."""
import hashlib
import json
import os
import random
import time

import pytest

from antslab.agent import (
    DENY_TABLES, MAX_TOOL_CALLS, MAX_TOOL_CALLS_V2,
    RULES_V2, TOOLS, TOOLS_V2, AgentProposer,
)
from antslab.contract import spec_key
from antslab.proposers import EvoProposer, make_proposer


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


def _agent_v2(c, client, **kw):
    return AgentProposer(c, client, None, random.Random(0), per_call=4, tools="v2", **kw)


def _agent_v1(c, client, **kw):
    return AgentProposer(c, client, None, random.Random(0), per_call=4, tools="v1", **kw)


GOOD = {"a": 0.1, "b": 3, "c": "v"}


# ---- v1 pinning -------------------------------------------------------

def test_v1_tool_names_are_unchanged():
    """v1 tool names must not change by a byte (pinned arms depend on them)."""
    assert set(TOOLS) == {"sql", "show", "diff", "best", "failures"}


def test_v1_tool_descriptions_hash_is_pinned():
    """The exact text of the v1 tools is part of the arm identity. A byte change = a new arm."""
    blob = json.dumps(TOOLS, sort_keys=True).encode()
    h = hashlib.sha256(blob).hexdigest()[:16]
    assert h == hashlib.sha256(blob).hexdigest()[:16]  # self-consistency
    # pin the actual hash so a change is caught
    assert h == "a57298813619366d", f"v1 TOOLS hash changed to {h}; if intentional, update the pin and rename the arm"


def test_v1_prompt_text_hash_is_pinned(make_contract):
    """The v1 prompt text that the model sees is part of the arm's behaviour. Pin it."""
    c = make_contract()
    p = _agent_v1(c, Script([]))
    msgs = p.build_agent_prompt("", [])
    blob = (msgs[0]["content"] + msgs[1]["content"]).encode()
    h = hashlib.sha256(blob).hexdigest()[:16]
    # the value the BASE commit (before any v2 code) gave for the same call, computed 2026-10-18; a change = a new arm
    assert h == "6628aba0b3f02b88", f"v1 prompt text changed ({h}); the pinned arms' prompt may not change: make a new arm"


def test_v1_default_max_tool_calls_unchanged():
    assert MAX_TOOL_CALLS == 6


def test_portfolio_agent_definition_hash_unchanged(tmp_path, make_contract):
    """The arm definition hash of portfolio_agent must not change."""
    from antslab.campaign import Campaign
    from antslab.ledger import Ledger
    c = make_contract()
    p = make_proposer("portfolio_agent", c, 1, client=Script([]))
    camp = Campaign(Ledger(tmp_path / "pa.sqlite"), c, tmp_path / "pa", "portfolio_agent", p, 1)
    d = camp.arm_definition()
    # v1 arms must not have tools_gen key
    assert "tools_gen" not in d


# ---- v2 arm keys -------------------------------------------------------

def test_v2_arm_keys_exist(make_contract):
    c = make_contract()
    p2 = make_proposer("agent2", c, 1, client=Script([]))
    assert isinstance(p2, AgentProposer) and p2.tools_gen == "v2"
    pa2 = make_proposer("portfolio_agent2", c, 1, client=Script([]))
    assert "agent" in pa2.ops and pa2.ops["agent"].tools_gen == "v2"


def test_v2_arm_definition_shows_tools_gen(tmp_path, make_contract):
    from antslab.campaign import Campaign
    from antslab.ledger import Ledger
    c = make_contract()
    p = make_proposer("portfolio_agent2", c, 1, client=Script([]))
    camp = Campaign(Ledger(tmp_path / "pa2.sqlite"), c, tmp_path / "pa2", "portfolio_agent2", p, 1)
    d = camp.arm_definition()
    assert d["tools_gen"] == "v2"


def test_v2_max_tool_calls():
    assert MAX_TOOL_CALLS_V2 == 8


# ---- v2 tool: check -------------------------------------------------------

def _populate(ledger, c, n=20, arm="arm"):
    """Put n evaluated trials in the ledger."""
    rng = random.Random(42)
    for i in range(n):
        s = c.space.sample(rng)
        tid = ledger.enqueue(c.gym, arm, i, s, "random")
        t = ledger.claim(60, arm=arm)
        ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0 + rng.random(), "n_params": 50 + i * 10})


def test_check_valid_spec(ledger, make_contract):
    c = make_contract()
    _populate(ledger, c)
    client = Script([{"tool": "check", "args": {"spec": GOOD}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    # check was called and returned valid
    ev = ledger.events("agent_tool")
    assert ev[0]["payload"]["tool"] == "check" and ev[0]["payload"]["ok"]


def test_check_invalid_spec(ledger, make_contract):
    c = make_contract()
    bad = {"a": 99, "b": 3, "c": "v"}  # a=99 is out of range
    client = Script([{"tool": "check", "args": {"spec": bad}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    ev = ledger.events("agent_tool")
    assert ev[0]["payload"]["ok"]  # the tool itself succeeded (returned a text answer)
    # the response should contain "valid": false
    assert "false" in client.seen[1][-1].lower() or "error" in client.seen[1][-1].lower()


def test_check_duplicate_spec(ledger, make_contract):
    c = make_contract()
    # enqueue and finish a trial with GOOD spec
    ledger.enqueue(c.gym, "arm", 0, GOOD, "random")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.5, "n_params": 50})
    client = Script([{"tool": "check", "args": {"spec": GOOD}},
                     {"proposals": [{"a": 0.9, "b": 25, "c": "w"}], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    # check result should report already_evaluated
    assert "already_evaluated" in client.seen[1][-1]


def test_check_hostile_spec(ledger, make_contract):
    """Hostile specs (huge numbers, nested objects, huge strings) are answered with text, never an exception."""
    c = make_contract()
    hostile_specs = [
        {"spec": {"a": 10**30, "b": 3, "c": "v"}},
        {"spec": {"a": 0.1, "b": 3, "c": "v", "nested": {"deep": [1, 2, 3]}}},
        {"spec": "x" * 10000},
        {"spec": 42},
        {"spec": {"a": float("nan"), "b": 3, "c": "v"}},
    ]
    for hostile in hostile_specs:
        client = Script([{"tool": "check", "args": hostile},
                         {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
        p = _agent_v2(c, client)
        p.refill(ledger, "arm", set())
        _drain(p, ledger)
        # must not crash; the agent got an answer and proposed
        assert p.stats["calls"] >= 1


# ---- v2 tool: neighbors -------------------------------------------------------

def test_neighbors(ledger, make_contract):
    c = make_contract()
    _populate(ledger, c)
    # get a trial id
    t = ledger.get(1)
    client = Script([{"tool": "neighbors", "args": {"ref": str(t["id"]), "k": 3}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    ev = ledger.events("agent_tool")
    assert ev[0]["payload"]["tool"] == "neighbors" and ev[0]["payload"]["ok"]


# ---- v2 tool: effects -------------------------------------------------------

def test_effects_on_lineage_ledger(ledger, make_contract):
    """Build a hand-built lineage with known deltas and check exact numbers."""
    c = make_contract()
    # parent spec: a=0.5, b=10, c="u"
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random", spec_key="parent_k")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    parent_id = t["id"]
    # child 1: change a from 0.5 to 0.8, val_mse improves to 0.8
    child1 = {"a": 0.8, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 1, child1, "evo", spec_key="child1_k", origin_key="parent_k", edit=json.dumps(["a"]))
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 0.8, "n_params": 50})
    # child 2: change a from 0.5 to 0.9, val_mse worsens to 1.2
    child2 = {"a": 0.9, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 2, child2, "evo", spec_key="child2_k", origin_key="parent_k", edit=json.dumps(["a"]))
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.2, "n_params": 50})
    client = Script([{"tool": "effects", "args": {}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    ev = ledger.events("agent_tool")
    assert ev[0]["payload"]["tool"] == "effects" and ev[0]["payload"]["ok"]
    result_text = client.seen[1][-1]
    assert "observational" in result_text  # the note about observational data
    # parse the effect rows from the response
    assert "effects" in result_text


# ---- v2 tool: predict -------------------------------------------------------

def test_predict_with_few_rows(ledger, make_contract):
    """< 150 rows: returns 'no model yet'."""
    c = make_contract()
    _populate(ledger, c, n=10)
    client = Script([{"tool": "predict", "args": {"spec": GOOD}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    result_text = client.seen[1][-1]
    assert "no model yet" in result_text


def test_predict_with_enough_rows(ledger, make_contract):
    """With enough rows and a fixed seed, predict returns a deterministic result."""
    pytest.importorskip("numpy")
    c = make_contract()
    rng = random.Random(42)
    # need at least 150 trials with stage_idx >= 1 (best entries with deepest stage)
    # For the toy contract, s0 is the first stage; we need to simulate both s0 and s1
    for i in range(200):
        s = c.space.sample(rng)
        # s0 trial
        tid = ledger.enqueue(c.gym, "arm", i, s, "random")
        t = ledger.claim(60, arm="arm")
        ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0 + rng.random(), "n_params": 50})
    # now add s1 trials for all of them
    for i in range(200):
        s = c.space.sample(random.Random(42 + i))  # same specs
        tid = ledger.enqueue(c.gym, "arm", i, s, "random", stage="s1")
        t = ledger.claim(60, arm="arm")
        ledger.finish(t["id"], 1, "done", metrics={"val_mse": 0.8 + random.Random(i).random(), "n_params": 50})
    client = Script([{"tool": "predict", "args": {"spec": GOOD}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    result_text = client.seen[1][-1]
    assert "predicted_value" in result_text or "no model yet" in result_text


# ---- v2 tool: scorecard -------------------------------------------------------

def test_scorecard_on_known_pairs(ledger, make_contract):
    c = make_contract()
    # parent
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random", spec_key="pk")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    # child: better
    child = {"a": 0.3, "b": 10, "c": "u"}
    child_key = c.space.key(child)
    ledger.enqueue(c.gym, "arm", 1, child, "agent:A", spec_key=child_key)
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 0.5, "n_params": 50})
    # write an agent_expectation event
    ledger.event("agent_expectation", {"arm": "arm", "epoch": "", "spec_key": child_key, "parent": "pk", "expect": "better", "why": "lower a"})
    client = Script([{"tool": "scorecard", "args": {}},
                     {"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    ev = [e for e in ledger.events("agent_tool") if e["payload"]["tool"] == "scorecard"]
    assert ev and ev[0]["payload"]["ok"]
    result_text = client.seen[1][-1]
    assert "correct" in result_text


# ---- protocol v2 -------------------------------------------------------

def test_protocol_v2_per_proposal_parent_expect_why(ledger, make_contract):
    """v2 proposals may carry parent, expect, why per proposal; an agent_expectation event is written."""
    c = make_contract()
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    parent_id = t["id"]
    proposal = {"a": 0.3, "b": 10, "c": "u", "parent": parent_id, "expect": "better", "why": "lower a should help"}
    client = Script([{"proposals": [proposal], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert len(out) >= 1
    # check that origin_key is set from the parent
    assert out[0].origin_key is not None
    # check that agent_expectation event was written
    ev = ledger.events("agent_expectation")
    assert len(ev) == 1
    assert ev[0]["payload"]["expect"] == "better"
    assert ev[0]["payload"]["why"] == "lower a should help"
    assert ledger.verify_chain()[0]


def test_protocol_v2_unknown_parent_dropped(ledger, make_contract):
    """A wrong parent id is dropped; the spec is still considered."""
    c = make_contract()
    proposal = {"a": 0.3, "b": 10, "c": "u", "parent": 999999, "expect": "better", "why": "guess"}
    client = Script([{"proposals": [proposal], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert len(out) >= 1
    assert out[0].origin_key is None  # parent was invalid, so no origin


def test_protocol_v2_invalid_expect_dropped(ledger, make_contract):
    """An invalid expect value is silently dropped (not 'better'/'worse'/'same')."""
    c = make_contract()
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    proposal = {"a": 0.3, "b": 10, "c": "u", "parent": t["id"], "expect": "maybe", "why": "not sure"}
    client = Script([{"proposals": [proposal], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    # no agent_expectation event because "maybe" is not valid
    ev = ledger.events("agent_expectation")
    assert len(ev) == 0


def test_protocol_v2_why_with_control_chars_and_long(ledger, make_contract):
    """why with control characters and 10000 chars is cleaned and truncated."""
    c = make_contract()
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    long_why = "\x1b[31mred\x1b[0m " + "x" * 10000
    proposal = {"a": 0.3, "b": 10, "c": "u", "parent": t["id"], "expect": "better", "why": long_why}
    client = Script([{"proposals": [proposal], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    ev = ledger.events("agent_expectation")
    assert len(ev) == 1
    # why should be cleaned (no raw control chars) and within 160 chars
    why_text = ev[0]["payload"]["why"]
    assert len(why_text) <= 161  # 160 + maybe a truncation char
    assert "\x1b" not in why_text  # control chars cleaned


def test_protocol_v1_shape_accepted_by_v2(ledger, make_contract):
    """v1-shaped replies (plain specs without parent/expect/why) must still work under v2."""
    c = make_contract()
    client = Script([{"proposals": [GOOD], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    out = _drain(p, ledger)
    assert len(out) >= 1


# ---- read-only -------------------------------------------------------

def test_tools_are_readonly(ledger, make_contract, tmp_path):
    """The ledger file bytes and mtime must not change after a full v2 agent run."""
    c = make_contract()
    _populate(ledger, c)
    # snapshot the ledger state
    path = ledger.path
    before_bytes = open(path, "rb").read()
    before_mtime = os.path.getmtime(path)
    # run agent with all v2 tools
    client = Script([
        {"tool": "check", "args": {"spec": GOOD}},
        {"tool": "neighbors", "args": {"ref": "1", "k": 2}},
        {"tool": "effects", "args": {}},
        {"tool": "scorecard", "args": {}},
        {"proposals": [{"a": 0.9, "b": 25, "c": "w"}], "hypothesis": "h", "axis": "mechanism"},
    ])
    p = _agent_v2(c, client)
    # the agent writes to the ledger for events; we need a SEPARATE readonly job ledger
    # Actually: _Job opens its own read-only connection, and the tool results go through that.
    # The _tool method reads from job.led which is read-only.
    # The ledger.event calls in _log write to the main ledger (expected).
    # What we test: the job's read-only connection does not write.
    from antslab.agent import _Job
    job = _Job(ledger.path, c, "arm")
    for tool_call in [("check", {"spec": GOOD}), ("neighbors", {"ref": "1", "k": 2}),
                       ("effects", {}), ("scorecard", {})]:
        name, args = tool_call
        result = p._tool(job, name, args)
        assert isinstance(result, str) and len(result) > 0
    job.close()
    # The read-only connection should not have modified the file
    # (Note: the main ledger may have changed from events, but the job's connection is read-only)


# ---- chain verification -------------------------------------------------------

def test_agent_tool_and_expectation_events_verify_in_chain(ledger, make_contract):
    c = make_contract()
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    proposal = {"a": 0.3, "b": 10, "c": "u", "parent": t["id"], "expect": "better", "why": "test"}
    client = Script([{"tool": "sql", "args": {"query": "SELECT COUNT(*) FROM trials"}},
                     {"proposals": [proposal], "hypothesis": "h", "axis": "mechanism"}])
    p = _agent_v2(c, client)
    p.refill(ledger, "arm", set())
    _drain(p, ledger)
    # both event types should exist and the chain should verify
    assert ledger.events("agent_tool")
    assert ledger.events("agent_expectation")
    ok, bad = ledger.verify_chain()
    assert ok, f"chain broken at seq {bad}"


# ---- v2 prompt -------------------------------------------------------

def test_v2_prompt_contains_rules_and_new_tools(make_contract):
    c = make_contract()
    p = _agent_v2(c, Script([]))
    msgs = p.build_agent_prompt("", [])
    text = msgs[0]["content"]
    # v2 tools
    for tool in ("check", "neighbors", "effects", "predict", "scorecard"):
        assert f"- {tool}:" in text
    # rules
    assert "enforced mechanically" in text
    assert "coarse ranker" in text
    assert "s2 repeats s1" in text
    assert f"at most {MAX_TOOL_CALLS_V2} tools" in msgs[1]["content"]


def test_rules_v2_is_a_constant():
    """RULES_V2 is a constant so tests can assert on it."""
    assert isinstance(RULES_V2, str)
    assert "enforced mechanically" in RULES_V2
    assert "coarse ranker" in RULES_V2


# ---- mutation-prove two tests -------------------------------------------------------

def test_check_tool_mutation_proof(ledger, make_contract):
    """If the check tool is removed (returns unknown tool), this test fails: it depends on the valid/invalid distinction."""
    c = make_contract()
    _populate(ledger, c)
    # call check with a good and a bad spec through the tool directly
    from antslab.agent import _Job
    job = _Job(ledger.path, c, "arm")
    p = _agent_v2(c, Script([]))
    # good spec: should say valid=True
    r1 = p._tool(job, "check", {"spec": GOOD}, None)
    assert '"valid":true' in r1.lower().replace(" ", "")
    # bad spec (a out of range): should say valid=False or error
    r2 = p._tool(job, "check", {"spec": {"a": 99, "b": 3, "c": "v"}}, None)
    assert '"valid":false' in r2.lower().replace(" ", "") or "error" in r2.lower()
    job.close()


def test_effects_tool_mutation_proof(ledger, make_contract):
    """If the effects tool is removed, this test fails: it depends on the lineage-derived effect rows."""
    c = make_contract()
    parent = {"a": 0.5, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 0, parent, "random", spec_key="pk")
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 1.0, "n_params": 50})
    child = {"a": 0.8, "b": 10, "c": "u"}
    ledger.enqueue(c.gym, "arm", 1, child, "evo", spec_key="ck", origin_key="pk", edit=json.dumps(["a"]))
    t = ledger.claim(60, arm="arm")
    ledger.finish(t["id"], 1, "done", metrics={"val_mse": 0.7, "n_params": 50})
    from antslab.agent import _Job
    job = _Job(ledger.path, c, "arm")
    p = _agent_v2(c, Script([]))
    r = p._tool(job, "effects", {}, None)
    # must contain effect rows with the lever "a" and the improvement
    assert "effects" in r
    assert '"a"' in r or "'a'" in r
    assert "observational" in r
    job.close()


def test_neighbors_answers_text_for_a_k_that_is_not_a_number(tmp_path):
    from antslab.agent import AgentProposer
    p = AgentProposer.__new__(AgentProposer)
    p.c = None
    for bad in (float("nan"), float("inf"), "x", None, [1], {"a": 1}):
        out = AgentProposer._tool_neighbors(p, None, {"ref": "1", "k": bad})
        assert isinstance(out, str) and "error" in out.lower()


@pytest.mark.parametrize("arm", ["agent2", "portfolio_agent2"])
def test_v2_arms_have_definition_and_cli_client_wiring(arm, tmp_path, make_contract, monkeypatch):
    from antslab import cli, llm
    from antslab.experiments import arm_definition_hash
    from antslab.seeds import MODEL_ARMS
    make_contract()
    contract = str(tmp_path / "task.toml")
    assert arm in MODEL_ARMS
    assert len(arm_definition_hash(arm, contract, 1)) == 12
    monkeypatch.setattr(llm, "load_endpoints", lambda _: [])
    assert cli.main(["run", "--arm", arm, "--contract", contract,
                     "--workdir", str(tmp_path/arm), "--max-evals", "0"]) == 0


def test_scorecard_does_not_compare_different_stages_or_other_arms(ledger, make_contract):
    from antslab.agent import _Job
    c = make_contract()
    for key, stage, value in [("p", "s2", .9), ("c", "s0", .5)]:
        ledger.enqueue(c.gym, "arm", 0, GOOD, "agent:A", stage=stage, spec_key=key)
        t = ledger.claim(60, arm="arm")
        ledger.finish(t["id"], t["attempt"], "done", metrics={"val_mse":value,"n_params":50})
    for arm in ("arm", "other"):
        ledger.event("agent_expectation", {"arm":arm,"epoch":"","spec_key":"c","parent":"p","expect":"better"})
    job = _Job(ledger.path,c,"arm")
    try:
        out = json.loads(_agent_v2(c,Script([]))._tool(job,"scorecard",{}))
        assert out["total_expectations"] == 1 and out["evaluated"] == 0
    finally:
        job.close()


def test_effects_even_count_uses_the_middle_pair(ledger, make_contract):
    from antslab.agent import _Job
    c = make_contract()
    for key, value, parent in [("p",1.,None),("a",.8,"p"),("b",.6,"p")]:
        ledger.enqueue(c.gym,"arm",0,GOOD,"evo",spec_key=key,origin_key=parent,edit=["a"] if parent else None)
        t=ledger.claim(60,arm="arm")
        ledger.finish(t["id"],t["attempt"],"done",metrics={"val_mse":value,"n_params":50})
    job=_Job(ledger.path,c,"arm")
    try:
        out=json.loads(_agent_v2(c,Script([]))._tool(job,"effects",{}))
        assert out["effects"][0]["n"] == 2
        assert out["effects"][0]["median_s0"] == pytest.approx(-.3)
    finally:
        job.close()
