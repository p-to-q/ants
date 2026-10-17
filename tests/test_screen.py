import json
import random
from pathlib import Path

import numpy as np
import pytest

from antslab.contract import load_contract
from antslab.ledger import Ledger
from antslab.screen import Encoder, ExtraTrees, ScreenPromoter, unit

REAL = Path(__file__).resolve().parents[1] / "contracts" / "tinyts_real.toml"


def _spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def test_extra_trees_rank_a_held_out_set_and_are_reproducible():
    rng = np.random.default_rng(0)
    X = rng.uniform(size=(900, 6))
    y = 3 * X[:, 0] + np.sin(6 * X[:, 1]) + (X[:, 2] > 0.5) * 1.5 + 0.05 * rng.normal(size=900)
    a = ExtraTrees(40, seed=1).fit(X[:700], y[:700])
    b = ExtraTrees(40, seed=1).fit(X[:700], y[:700])
    mu, sd = a.predict(X[700:])
    assert _spearman(mu, y[700:]) > 0.9
    assert np.array_equal(mu, b.predict(X[700:])[0])                      # the same seed gives the same model
    assert (sd >= 0).all() and sd.max() > 0                              # the trees disagree somewhere: there is an uncertainty


def test_extra_trees_survive_degenerate_input():
    X = np.ones((30, 4))
    mu = ExtraTrees(5).fit(X, np.arange(30.0)).predict(X)[0]
    assert np.ptp(mu) == 0 and 10 < mu[0] < 19                       # no feature varies: every tree is one leaf (the mean of its subsample)
    assert ExtraTrees(5).fit(np.random.default_rng(0).uniform(size=(30, 4)), np.full(30, 2.0)).predict(X[:2])[0].tolist() == [2.0, 2.0]


def test_encoder_has_a_fixed_width_and_marks_missing_parameters():
    c = load_contract(REAL)
    enc = Encoder(c.space)
    spec = {p.name: p.default_value() for p in c.space.params.values()}
    v = enc.vector(spec, {"rel_mse": 0.9, "rel_mae": 1.0, "val_mse": 0.7, "n_params": 1000})
    assert v.shape == (enc.width,) and np.isfinite(v).all()
    del spec["ridge"]
    v2 = enc.vector(spec, {"rel_mse": 0.9})
    assert v2.shape == v.shape and (v2 == -1.0).sum() > (v == -1.0).sum()
    onehot = [i for i, n in enumerate(enc.names) if n.startswith("core=")]
    assert v[onehot].sum() == 1.0                                         # exactly one core is on


def test_the_random_share_is_a_pure_function_of_seed_and_trial():
    assert unit(3, 17) == unit(3, 17) and unit(3, 17) != unit(3, 18) and unit(3, 17) != unit(4, 17)
    u = [unit(1, i) for i in range(4000)]
    assert 0.45 < sum(u) / len(u) < 0.55 and 0 <= min(u) and max(u) < 1


def test_the_definition_changes_with_every_setting():
    c = load_contract(REAL)
    base = ScreenPromoter(c, 1).definition()
    assert base["rule"] == "screen"
    for kw in ({"explore": 0.2}, {"kappa": 1.0}, {"min_rows": 99}, {"refit_every": 7}, {"window": 50}, {"n_trees": 10}):
        assert ScreenPromoter(c, 1, **kw).definition() != base
    with pytest.raises(ValueError):
        ScreenPromoter(c, 1, explore=1.0)


def _toy_campaign(make_contract, tmp_path, monkeypatch):
    from antslab.campaign import Campaign
    from antslab.proposers import RandomProposer
    c = make_contract()
    led = Ledger(tmp_path / "w" / "ledger.sqlite")
    p = RandomProposer(c, random.Random(4))
    p.promoter = ScreenPromoter(c, 4, min_rows=12, refit_every=10, n_trees=8)
    return c, led, Campaign(led, c, tmp_path / "w", "random", p, 4, batch=4)


def test_the_screen_decides_through_the_campaign_logs_every_decision_and_keeps_its_workload(make_contract, tmp_path, monkeypatch):
    # The toy gym has three stages and a cheap, noisy first one. Start with the quantile rule (cold), then the model takes over.
    c, led, camp = _toy_campaign(make_contract, tmp_path, monkeypatch)
    assert camp.promoter is camp.p.promoter and camp.arm_definition()["promotion"]["rule"] == "screen"
    for _ in range(260):
        if camp.step().startswith("budget"):
            break
    ev = led.events("screen_decision")
    modes = {e["payload"]["mode"] for e in ev}
    assert "cold" in modes and "model" in modes, modes                       # it started cold and the model took over
    assert camp.promoter.fits >= 1 and camp.stats["screen_fits"] == camp.promoter.fits
    s0 = led.conn.execute("SELECT COUNT(*) FROM trials WHERE stage='s0' AND state='done'").fetchone()[0]
    s1 = led.conn.execute("SELECT COUNT(*) FROM trials WHERE stage='s1'").fetchone()[0]
    assert len(ev) <= s0 and 0.1 < s1 / s0 < 0.8                              # a decision per first-stage result; a share, not all and not none
    model = [e["payload"] for e in ev if e["payload"]["mode"] == "model"]
    assert model and all(m["p"] in (1.0, camp.promoter.explore) for m in model)       # the probability of every model decision is logged and exact
    assert any(m["promoted"] and not m["main"] for m in model) or len(model) < 40     # the random share promotes some the model would not
    ok, bad = led.verify_chain()
    assert ok, bad


def test_a_failing_model_falls_back_to_the_quantile_rule(make_contract, tmp_path, monkeypatch):
    c, led, camp = _toy_campaign(make_contract, tmp_path, monkeypatch)
    monkeypatch.setattr("antslab.screen.ExtraTrees.fit", lambda self, X, y: (_ for _ in ()).throw(RuntimeError("boom")))
    for _ in range(120):
        if camp.step().startswith("budget"):
            break
    assert camp.promoter.model is None and camp.stats.get("screen_fit_errors", 0) >= 1
    assert led.events("screen_decision") and {e["payload"]["mode"] for e in led.events("screen_decision")} == {"cold"}
    assert led.conn.execute("SELECT COUNT(*) FROM trials WHERE stage='s1'").fetchone()[0] > 0       # promotion never stopped


def test_arms_that_already_have_ledgers_keep_the_definition_the_server_recorded(tmp_path):
    """Epoch 2 started with `portfolio` at definition hash 67618a671563 (all nine operators, a plain bandit, no reserved pair). The default of
    build_portfolio moved on (noisy operators dropped, a reserved pair, a discounted bandit); that design is the arm `portfolio_lean`. A change of the
    plain arm's definition would make a restart of the running service a different experiment under the same name (docs/upgrading.md)."""
    from antslab.campaign import Campaign
    from antslab.proposers import make_proposer
    c = load_contract(REAL)
    hashes = {}
    for arm in ("portfolio", "portfolio_lean", "portfolio_screen"):
        camp = Campaign(Ledger(tmp_path / f"{arm}.sqlite"), c, tmp_path / arm, arm, make_proposer(arm, c, 1), 1)
        hashes[arm] = camp.start_record()["arm_def_hash"]
    assert hashes["portfolio"] == "67618a671563"
    assert len(set(hashes.values())) == 3
    d = Campaign(Ledger(tmp_path / "x.sqlite"), c, tmp_path / "x", "portfolio_lean", make_proposer("portfolio_lean", c, 1), 1).arm_definition()
    assert "tpe" in d["operators"] and not {"aco", "sa", "sweep"} & set(d["operators"]) and d["use_discount"] is True and d["reserve"]


def test_the_plain_portfolio_proposes_what_version_0_6_0_proposed():
    """The sequence of specifications the arm `portfolio` draws on the analytic benchmark, hashed. Computed with the sources of 0.6.0 and with this
    tree: identical (2026-10-17). If this fails the arm changed; rename it (new arm) rather than update the number."""
    import hashlib
    from antslab import bench
    from antslab.proposers import make_proposer
    r = bench.drive(lambda cc, s: make_proposer("portfolio", cc, s), bench.make_contract(), 400, 3, noise=0.01, checkpoints=(400,))
    r["hist"].update()
    assert hashlib.sha256(json.dumps(sorted(r["hist"].first.keys())).encode()).hexdigest()[:16] == "6eaa7b38d3ac1602"
    assert round(r["best"][400], 6) == 0.232864


def test_every_operator_allocation_is_logged_with_its_probabilities(make_contract, tmp_path):
    """The decision log (roadmap step 35): the probability each operator had for each draw, the version of the rule, the counts drawn."""
    from antslab.campaign import Campaign
    from antslab.portfolio import build_portfolio
    c = make_contract()
    led = Ledger(tmp_path / "w" / "ledger.sqlite")
    camp = Campaign(led, c, tmp_path / "w", "portfolio", build_portfolio(c, 1, with_tpe=False), 1, batch=4)
    for _ in range(25):
        camp.step()
    ev = [e["payload"] for e in led.events("operator_alloc")]
    assert ev and all(e["arm"] == "portfolio" and e["policy"].startswith("bandit:") for e in ev)
    for e in ev:
        assert abs(sum(e["probs"].values()) - 1.0) < 0.01 and sum(e["counts"].values()) == e["k"]
    assert led.verify_chain()[0]
