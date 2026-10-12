"""Red team: what the hash chain does and does not detect, and the external anchor that closes the gap."""
import hashlib
import json

import pytest

from antslab.ledger import GENESIS, GateError, Ledger, canon


def _fill(ledger, n=6):
    for i in range(n):
        ledger.event("e", {"x": i})


def _rewrite_from(ledger, seq, new_payload):
    """The strongest attacker: edit event `seq` and recompute EVERY later hash so the chain is internally consistent."""
    rows = ledger.conn.execute("SELECT * FROM events WHERE seq>=? ORDER BY seq", (seq,)).fetchall()
    prev = ledger.conn.execute("SELECT hash FROM events WHERE seq=?", (seq - 1,)).fetchone()
    prev = prev[0] if prev else GENESIS
    for r in rows:
        payload = canon(new_payload) if r["seq"] == seq else r["payload"]
        h = Ledger._hash(prev, r["ts"], r["kind"], payload)
        ledger.conn.execute("UPDATE events SET payload=?, prev_hash=?, hash=? WHERE seq=?", (payload, prev, h, r["seq"]))
        prev = h


def test_tail_truncation_is_invisible_to_the_chain_but_caught_by_an_anchor(ledger):
    _fill(ledger)
    a = ledger.anchor()
    assert a.seq == 6 and a.head == ledger.head() and ledger.verify_against_anchor(a) == (True, None)
    ledger.conn.execute("DELETE FROM events WHERE seq>3")                # drop the last 3 events (e.g. a sealed look)
    assert ledger.verify_chain() == (True, None) and ledger.verify_tail() == (True, None)     # the chain alone cannot tell
    ok, why = ledger.verify_against_anchor(a)
    assert not ok and "truncated" in why


def test_consistent_rewrite_is_invisible_to_the_chain_but_caught_by_an_anchor(ledger):
    _fill(ledger)
    a = ledger.anchor()
    _rewrite_from(ledger, 2, {"x": "forged"})
    assert ledger.verify_chain() == (True, None) and ledger.verify_tail() == (True, None)
    ok, why = ledger.verify_against_anchor(a)
    assert not ok and "rewritten" in why


def test_rewrite_after_the_anchor_is_only_caught_by_a_later_anchor(ledger):
    _fill(ledger, 4)
    old = ledger.anchor()
    _fill(ledger, 3)
    new = ledger.anchor()
    _rewrite_from(ledger, 6, {"x": "forged"})
    assert ledger.verify_against_anchor(old) == (True, None)             # honest about its reach
    assert ledger.verify_against_anchor(new)[0] is False


def test_truncate_then_append_leaves_a_sequence_gap_that_verify_chain_reports(ledger):
    _fill(ledger)
    ledger.conn.execute("DELETE FROM events WHERE seq>4")                # AUTOINCREMENT keeps counting from 6
    ledger.event("e", {"x": "after the cut"})
    ok, bad = ledger.verify_chain()
    assert not ok and bad == 7
    assert ledger.verify_tail()[0] is False


def test_reordered_rows_are_detected(ledger):
    _fill(ledger)
    ledger.conn.execute("UPDATE events SET seq=100 WHERE seq=3")
    ledger.conn.execute("UPDATE events SET seq=3 WHERE seq=4")
    ledger.conn.execute("UPDATE events SET seq=4 WHERE seq=100")
    assert ledger.verify_chain()[0] is False and ledger.verify_tail()[0] is False


def test_tail_check_links_its_first_row_to_the_row_before_the_window(ledger):
    _fill(ledger, 8)
    ledger.conn.execute("DELETE FROM events WHERE seq=3")               # older than the window of the last 4 rows (+1 link row)
    assert ledger.verify_tail(4)[0] is True                              # outside the window: verify_chain's job, not the tail's
    ledger.conn.execute("DELETE FROM events WHERE seq=4")               # the row that precedes the window is gone
    assert ledger.verify_tail(4)[0] is False


def test_tail_window_covering_the_whole_chain_checks_the_genesis(ledger):
    _fill(ledger, 3)
    ledger.conn.execute("DELETE FROM events WHERE seq=1")
    assert ledger.verify_tail(50)[0] is False


def test_anchor_file_round_trip_and_detection(ledger, tmp_path):
    f = tmp_path / "anchors.jsonl"
    _fill(ledger, 3); ledger.write_anchor(f)
    _fill(ledger, 3); ledger.write_anchor(f)
    assert ledger.verify_anchor_file(f) == (True, None)
    ledger.conn.execute("DELETE FROM events WHERE seq>4")
    ok, why = ledger.verify_anchor_file(f)
    assert not ok and "anchor line 2" in why


def test_empty_ledger_anchor(ledger):
    a = ledger.anchor()
    assert a.seq == 0 and a.head == GENESIS and ledger.verify_against_anchor(a)[0]
    assert ledger.verify_against_anchor((0, "f" * 64, 0.0))[0] is False


def test_proof_events_cannot_be_written_through_the_public_event_api(ledger):
    for kind in ("sealed_result", "sealed_evaluation", "candidate_frozen", "model_status", "model_gates", "alpha_wealth"):
        with pytest.raises(GateError):
            ledger.event(kind, {"gym": "toy", "freeze_hash": "x", "metrics": {"val_mse": 0.0}})
    assert ledger.events() == []


def test_deleting_unseal_rows_does_not_refund_a_credit(ledger):
    fh = ledger.freeze("g", {"x": 1}, "code0", {})
    ledger.record_unseal("g", fh, 1, {})
    ledger.conn.execute("DELETE FROM unseals")                           # the table is mutable and outside the hash chain
    fh2 = ledger.freeze("g", {"x": 2}, "code0", {})
    from antslab.ledger import UnsealBudgetExceeded
    with pytest.raises(UnsealBudgetExceeded):
        ledger.record_unseal("g", fh2, 1, {})
    assert ledger.unseal_count("g") == 1
