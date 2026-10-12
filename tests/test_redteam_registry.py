"""Red team: the registry ladder - can a model be crowned without evidence, on different windows, or by luck?"""
import numpy as np
import pytest

from antslab.ledger import GateError
from antslab.registry import PromotionError, Registry, decide_promotion

from . import _rt as rt


@pytest.fixture
def reg(ledger):
    r = Registry(ledger)
    r.register_baseline("naive", "tinyts", 300, {"x": 0}, "k0")
    return r


def _model(reg, i=1):
    return reg.add(f"m{i}", "tinyts", 300, {"x": i}, f"k{i}")


# ---- no champion without a sealed result for exactly this spec ------------------------------------------------
def test_champion_needs_a_recorded_sealed_result_for_its_own_freeze_hash(reg, ledger):
    base = rt.base_losses()
    m = _model(reg)
    assert rt.validate(reg, m, 0.9, base, 1.0)["verdict"] == "accepted"
    with pytest.raises(PromotionError, match="freeze_hash"):
        reg.transition(m, "champion")                                              # no evidence at all
    with pytest.raises(PromotionError, match="freeze_hash"):
        reg.transition(m, "champion", {"freeze_hash": "0" * 64})                   # a hash nobody froze
    other = ledger.freeze("tinyts", {"x": 999}, "code0", {})
    with pytest.raises(PromotionError, match="not this model's spec"):
        reg.transition(m, "champion", {"freeze_hash": other})                      # frozen, but a different spec
    fh = ledger.freeze("tinyts", {"x": 1}, "code0", {})
    with pytest.raises(PromotionError, match="no recorded sealed result"):
        reg.transition(m, "champion", {"freeze_hash": fh})                         # frozen, never evaluated
    n = ledger.record_unseal("tinyts", fh, 2, {})
    with pytest.raises(PromotionError, match="no recorded sealed result"):
        reg.transition(m, "champion", {"freeze_hash": fh})                         # credit spent, run not finished
    ledger.record_sealed_result("tinyts", fh, n, None, "crashed")
    with pytest.raises(PromotionError, match="no recorded sealed result"):
        reg.transition(m, "champion", {"freeze_hash": fh})                         # the run failed
    assert reg.get(m)["status"] == "validated"


def test_champion_after_a_real_sealed_result_and_the_evidence_is_on_the_chain(reg, ledger):
    base = rt.base_losses()
    m = _model(reg)
    fh = rt.climb(reg, ledger, m, 0.9, base, 1.0)
    assert reg.champion("tinyts", 300)["id"] == m
    ev = ledger.events("model_status")[-1]["payload"]["evidence"]
    assert ev["freeze_hash"] == fh and ev["sealed_metrics"] == {"val_mse": 0.5}
    assert reg.verify() == [] and ledger.verify_chain()[0]


# ---- the forward steps cannot be taken with a bare transition ---------------------------------------------------
def test_validated_and_confirmed_cannot_be_granted_without_their_proofs(reg):
    m = _model(reg)
    with pytest.raises(PromotionError, match="confirm"):
        reg.transition(m, "confirmed")
    assert rt.confirm(reg, m)["passed"]
    with pytest.raises(PromotionError, match="challenge"):
        reg.transition(m, "validated", {"verdict": "accepted"})                    # self-issued evidence is not evidence
    assert reg.get(m)["status"] == "confirmed"


def test_confirm_gates_cap_reference_trivial_forecasters_finiteness_and_determinism(reg):
    ok = {"rel_mse": 0.9, "n_params": 250}
    cases = [
        ({**ok, "n_params": 301}, ok, {}, "exceeds cap"),
        ({**ok, "rel_mse": 1.0}, {**ok, "rel_mse": 1.0}, {}, "reference"),
        ({**ok, "rel_mse": float("nan")}, ok, {}, "non-finite"),
        (ok, {**ok, "rel_mse": 0.9001}, {}, "not reproducible"),
        (ok, ok, {"last_value": 0.8}, "trivial forecaster 'last_value'"),
    ]
    for i, (m, replay, triv, msg) in enumerate(cases):
        mid = _model(reg, i + 1)
        r = reg.confirm(mid, m, replay, trivial=triv)
        assert not r["passed"] and any(msg in f for f in r["fails"]), (msg, r)
        assert reg.get(mid)["status"] == "candidate"                                 # a failing model stays a candidate
    mid = _model(reg, 99)
    assert reg.confirm(mid, ok, dict(ok), trivial={"last_value": 0.95, "mean": 1.4})["passed"]
    assert reg.get(mid)["status"] == "confirmed"


# ---- identical windows, the right incumbent ----------------------------------------------------------------------------
def _recs(reg, m, base, f=0.9, inc_f=1.0, **kw):
    inc = reg.incumbent("tinyts", 300)
    return rt.record(reg, m, base, f, **kw), rt.record(reg, inc["id"], base, inc_f, **kw)


def test_challenge_on_different_windows_is_refused_without_spending_anything(reg, ledger):
    base = rt.base_losses()
    m = _model(reg); assert rt.confirm(reg, m)["passed"]
    cand, inc = _recs(reg, m, base)
    shifted = {f: [(f, "val", 96, i + 1) for i in range(len(v))] for f, v in base.items()}       # same count, other windows
    inc = rt.record(reg, inc["model_id"], base, 1.0, ids=shifted)
    d = reg.challenge(m, cand, inc)
    assert d["verdict"] == "failed" and "identical windows" in d["reason"]
    assert reg.get(m)["status"] == "confirmed"
    assert ledger.conn.execute("SELECT COUNT(*) FROM alpha_wealth").fetchone()[0] == 0     # a malformed challenge costs no alpha


def test_challenge_cannot_drop_the_families_it_loses_on(reg):
    base = rt.base_losses()
    m = _model(reg); assert rt.confirm(reg, m)["passed"]
    cand, inc = _recs(reg, m, base)
    cand["losses"].pop("ecl"); cand["windows"].pop("ecl")
    assert reg.challenge(m, cand, inc)["verdict"] == "failed"
    assert reg.get(m)["status"] == "confirmed"


def test_challenge_must_name_the_incumbent_of_the_bin_and_the_champion_wins_over_validated(reg, ledger):
    base = rt.base_losses()
    a, b = _model(reg, 1), _model(reg, 2)
    rt.climb(reg, ledger, a, 0.9, base, 1.0)                                         # a is champion, naive is a stale validated model
    assert rt.confirm(reg, b)["passed"]
    naive = next(r["id"] for r in reg.table("tinyts") if r["name"] == "naive")
    d = reg.challenge(b, rt.record(reg, b, base, 0.95), rt.record(reg, naive, base, 1.0))     # beat the weak one instead
    assert d["verdict"] == "failed" and "incumbent of cap bin" in d["reason"]
    d = reg.challenge(b, rt.record(reg, a, base, 0.8), rt.record(reg, a, base, 0.9))          # record of someone else
    assert d["verdict"] == "failed" and "different model" in d["reason"]
    assert reg.get(b)["status"] == "confirmed"


def test_challenge_across_epochs_or_without_mae_or_without_an_incumbent_is_refused(reg, ledger):
    base = rt.base_losses()
    m = _model(reg); assert rt.confirm(reg, m)["passed"]
    inc_id = reg.incumbent("tinyts", 300)["id"]
    d = reg.challenge(m, rt.record(reg, m, base, 0.9, epoch="e2"), rt.record(reg, inc_id, base, 1.0, epoch="e1"))
    assert d["verdict"] == "failed" and "epochs" in d["reason"]
    no_mae = {**rt.record(reg, m, base, 0.9), "mae": {}}
    d = reg.challenge(m, no_mae, rt.record(reg, inc_id, base, 1.0))
    assert d["verdict"] == "failed" and "MAE" in d["reason"]
    lonely = reg.add("x", "tinyts", 1000, {"x": 5}, "k5"); assert rt.confirm(reg, lonely, n_params=900)["passed"]
    d = reg.challenge(lonely, rt.record(reg, lonely, base, 0.9), rt.record(reg, inc_id, base, 1.0))
    assert d["verdict"] == "failed" and "no incumbent" in d["reason"]


def test_a_gain_that_only_exists_under_mse_is_not_accepted(reg):
    base = rt.base_losses()
    m = _model(reg); assert rt.confirm(reg, m)["passed"]
    inc_id = reg.incumbent("tinyts", 300)["id"]
    d = reg.challenge(m, rt.record(reg, m, base, 0.9, mae_factor=1.1), rt.record(reg, inc_id, base, 1.0))
    assert d["verdict"] != "accepted" and any("MAE" in r for r in d["reasons"])
    assert reg.get(m)["status"] != "validated"


def test_window_hash_refuses_duplicate_windows_and_length_mismatches():
    from antslab import metrics as M
    with pytest.raises(ValueError, match="duplicate"):
        M.window_hash([("a", 1), ("a", 1)])
    assert M.window_hash([("a", 1), ("a", 2)]) != M.window_hash([("a", 2), ("a", 1)])         # order matters: pairs are positional
    with pytest.raises(ValueError):
        Registry.loss_record(1, {"f": [1.0, 2.0]}, {"f": [("f", 1)]})


# ---- online FDR -----------------------------------------------------------------------------------------------------
def test_every_test_is_on_the_books_with_its_level_and_a_promotion_earns_alpha_back(reg, ledger):
    base = rt.base_losses()
    for i in range(1, 4):                                                            # three null challenges
        m = _model(reg, i)
        assert rt.validate(reg, m, 1.0, base, 1.0)["verdict"] in ("rejected", "unstable")
    m = _model(reg, 4)
    assert rt.validate(reg, m, 0.9, base, 1.0)["verdict"] == "accepted"
    rows = ledger.conn.execute("SELECT t,level,p_value,promoted FROM alpha_wealth ORDER BY t").fetchall()
    assert [r["t"] for r in rows] == [1, 2, 3, 4] and [r["promoted"] for r in rows] == [0, 0, 0, 1]
    assert rows[0]["level"] > rows[1]["level"] > rows[2]["level"] > 0
    assert len(ledger.events("alpha_wealth")) == 4
    m2 = _model(reg, 5)                                                              # next test: level is topped up by the promotion
    inc = reg.incumbent("tinyts", 300)
    reg.confirm(m2, {"rel_mse": 0.8, "n_params": 9}, {"rel_mse": 0.8, "n_params": 9})
    reg.challenge(m2, rt.record(reg, m2, base, 1.0), rt.record(reg, inc["id"], base, 1.0))
    lvl5 = ledger.conn.execute("SELECT level FROM alpha_wealth WHERE t=5").fetchone()[0]
    assert lvl5 > rows[2]["level"]


def test_a_clear_gain_is_still_promoted_after_a_long_run_of_failed_challenges(reg):
    base = rt.base_losses()
    for i in range(1, 41):
        assert rt.validate(reg, _model(reg, i), 1.0, base, 1.0)["verdict"] != "accepted"
    assert rt.validate(reg, _model(reg, 99), 0.9, base, 1.0)["verdict"] == "accepted"


def test_a_marginal_gain_that_the_plain_rule_accepts_is_refused_by_the_online_level_late_in_the_stream(reg, ledger):
    """After 19 failed challenges the 20th has a level of ~5e-5. A 1.2% gain with p ~5e-4 passes decide_promotion
    (the plain per-test rule) but is one of the lucky wins that multiple testing predicts, so it is not validated."""
    base = rt.base_losses(seed=5)
    for i in range(1, 20):
        assert rt.validate(reg, _model(reg, i), 1.0, base, 1.0)["verdict"] != "accepted"
    rng = np.random.default_rng(1)
    plain_cand = {f: np.clip(v * (1 - 0.012) + rng.normal(0, 0.3, len(v)), 0, None) for f, v in base.items()}
    assert decide_promotion(plain_cand, base)["verdict"] == "accepted"              # per-test rule alone: promoted
    m = _model(reg, 20); assert rt.confirm(reg, m)["passed"]
    inc = reg.incumbent("tinyts", 300)
    ids = {f: rt.window_ids(f, len(v)) for f, v in base.items()}
    mae = lambda L: {f: np.sqrt(v) for f, v in L.items()}
    d = reg.challenge(m, reg.loss_record(m, plain_cand, ids, mae(plain_cand)), reg.loss_record(inc["id"], base, ids, mae(base)))
    assert d["verdict"] == "unstable" and any("online-FDR" in r for r in d["reasons"])
    assert reg.get(m)["status"] == "unstable"
    row = ledger.conn.execute("SELECT p_value,level,promoted FROM alpha_wealth WHERE t=20").fetchone()
    assert row["promoted"] == 0 and row["p_value"] > row["level"] > 0


def test_the_fdr_parameters_are_pinned_by_the_first_recorded_test(ledger):
    base = rt.base_losses()
    r1 = Registry(ledger, fdr_alpha=0.01)
    r1.register_baseline("naive", "tinyts", 300, {"x": 0}, "k0")
    m = r1.add("m1", "tinyts", 300, {"x": 1}, "k1")
    rt.validate(r1, m, 1.0, base, 1.0)
    r2 = Registry(ledger, fdr_alpha=0.5)                                             # somebody loosens the procedure afterwards
    assert r2._fdr() == (0.01, 0.005)


def test_online_fdr_holds_over_2000_null_challenges_where_the_plain_rule_does_not(tmp_path):
    """20 independent streams x 100 challenges. Every challenger equals the incumbent plus autocorrelated noise (no true gain).
    The plain accept rule promotes noise ~1% of the time; with LORD++ at alpha=0.05 at most ~5% of STREAMS may contain any promotion."""
    from antslab.ledger import Ledger
    rng = np.random.default_rng(11)
    N, plain, bad_streams, promotions = 120, 0, 0, 0

    def noise(n, sd=0.6, rho=0.5):
        e = rng.normal(0, sd, n + 20); x = np.zeros(n + 20)
        for j in range(1, n + 20):
            x[j] = rho * x[j - 1] + e[j]
        return x[20:]

    for s in range(20):
        led = Ledger(tmp_path / f"l{s}.sqlite"); led.conn.execute("PRAGMA synchronous=OFF")
        reg = Registry(led)
        reg.register_baseline("naive", "g", 100, {"x": 0}, "k0")
        won = 0
        for k in range(100):
            inc = {f: rng.gamma(2, 1, N) for f in rt.FAMS}
            cand = {f: np.clip(inc[f] + noise(N), 0, None) for f in inc}
            plain += decide_promotion(cand, inc, block=12)["verdict"] == "accepted"
            m = reg.add(f"m{k}", "g", 100, {"x": k + 1}, f"k{k}")
            assert rt.confirm(reg, m)["passed"]
            ids = {f: rt.window_ids(f, N) for f in inc}
            mae = lambda L: {f: np.sqrt(v) for f, v in L.items()}
            cur = reg.incumbent("g", 100)
            reg.challenge(m, reg.loss_record(m, cand, ids, mae(cand)), reg.loss_record(cur["id"], inc, ids, mae(inc)), block=12)
            won += reg.get(m)["status"] == "validated"
        promotions += won; bad_streams += won > 0
    assert plain >= 12                                                               # the unprotected rule would have promoted noise
    assert bad_streams <= 2 and promotions <= 2                                      # nominal 5% of 20 streams = 1
    assert Registry(led)._fdr() == (0.05, 0.025)


def test_direct_edits_of_the_models_table_are_found_by_the_event_replay(reg, ledger):
    m = _model(reg)
    assert reg.verify() == []
    ledger.conn.execute("UPDATE models SET status='champion' WHERE id=?", (m,))     # the chain never saw this
    assert ledger.verify_chain()[0]
    out = reg.verify()
    assert len(out) == 1 and "champion" in out[0] and "candidate" in out[0]
