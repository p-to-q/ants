import json, os, random, signal, subprocess, sys, time
from pathlib import Path
import pytest
from antslab.campaign import Campaign
from antslab.proposers import make_proposer


def camp(ledger, c, tmp_path, arm="random", seed=0, **kw):
    return Campaign(ledger, c, tmp_path / "w", arm, make_proposer(arm, c, seed), seed, **kw)


def test_end_to_end_cascade_and_frontier(ledger, make_contract, tmp_path):
    c = make_contract()
    cp = camp(ledger, c, tmp_path, "evo", budget_evals=60)
    assert cp.run() == "budget"
    counts = {}
    for t in ledger.trials("toy", "evo"):
        counts[t["stage"]] = counts.get(t["stage"], 0) + (t["state"] == "done")
    assert counts["s0"] > counts["s1"] >= counts.get("s2", 0) >= 1          # cascade narrows
    fr = cp.frontier()
    assert fr["100"]["metrics"]["n_params"] <= 100 and fr["300"]["metrics"]["n_params"] <= 300
    assert ledger.verify_chain()[0]
    assert ledger.artifacts("kind='log'")                                    # logs were compressed + registered
    assert all(a["protected"] for a in ledger.artifacts("kind='metrics'"))


def test_same_seed_same_trajectory(make_contract, tmp_path):
    from antslab.ledger import Ledger
    runs = []
    for i in (0, 1):
        c = make_contract()
        l = Ledger(tmp_path / f"l{i}" / "ledger.sqlite")
        cp = Campaign(l, c, tmp_path / f"w{i}", "evo", make_proposer("evo", c, 5), 5, budget_evals=30)
        cp.run()
        runs.append([(t["spec_key"], t["stage"], t["metrics"] and t["metrics"]["val_mse"]) for t in l.trials()])
    assert runs[0] == runs[1]


def test_hard_cap_blocks_promotion(ledger, make_contract, tmp_path):
    c = make_contract()
    cp = camp(ledger, c, tmp_path, "random", budget_evals=40)
    cp.run()
    for t in ledger.trials("toy", "random", ("done",)):
        if t["metrics"]["n_params"] > 300:
            assert not [x for x in ledger.trials() if x["parent_id"] == t["id"]]


def test_disk_pause_starts_nothing(ledger, make_contract, tmp_path):
    c = make_contract()
    cp = camp(ledger, c, tmp_path, free_fn=lambda: 5.0)
    assert cp.step() == "paused:disk:hardstop" and not ledger.trials()
    cp = camp(ledger, c, tmp_path, free_fn=lambda: 7.0)
    assert cp.step() == "paused:disk:pause"
    cp = camp(ledger, c, tmp_path, free_fn=lambda: 11.0, budget_evals=1)
    assert cp.step().startswith("ran:")                                     # 'clean' level still runs


def test_cpu_budget_stops(ledger, make_contract, tmp_path):
    cp = camp(ledger, make_contract(), tmp_path, budget_cpu_s=0.0)
    assert cp.step() == "budget" and not ledger.trials()


def test_crash_then_retry_succeeds(ledger, make_contract, tmp_path, monkeypatch):
    monkeypatch.setenv("TOY_FAULT", "kill"); monkeypatch.setenv("TOY_FAULT_MOD", "3")
    c = make_contract()
    cp = camp(ledger, c, tmp_path, budget_evals=12)
    cp.run()
    ev = [e["kind"] for e in ledger.events()]
    assert "trial_requeued" in ev
    assert not [t for t in ledger.trials() if t["state"] == "running"]          # nothing left stuck mid-flight
    assert [t for t in ledger.trials() if t["attempt"] == 2 and t["state"] == "done"]   # a retried trial really finished
    assert all(t["state"] == "done" for t in ledger.trials() if t["seed"] % 3 == 0 and t["attempt"] == 2)
    assert ledger.verify_chain()[0]


def test_persistent_failure_opens_breaker(ledger, make_contract, tmp_path, monkeypatch):
    monkeypatch.setenv("TOY_FAULT", "exit"); monkeypatch.setenv("TOY_FAULT_MOD", "1")
    cp = camp(ledger, make_contract(), tmp_path, breaker_n=3, breaker_cooldown_s=3600, batch=1)
    out = [cp.step() for _ in range(6)]
    assert out[:3] == ["ran:1:failed", "ran:2:failed", "ran:3:failed"] and out[3] == "breaker"


def test_hang_is_killed_and_recorded(ledger, make_contract, tmp_path, monkeypatch):
    monkeypatch.setenv("TOY_FAULT", "hang"); monkeypatch.setenv("TOY_FAULT_MOD", "1")
    cp = camp(ledger, make_contract(timeout=2), tmp_path, batch=1)
    t = time.time()
    assert cp.step().endswith("timeout") and time.time() - t < 20


def test_nan_metrics_never_enter_the_ledger_as_results(ledger, make_contract, tmp_path, monkeypatch):
    monkeypatch.setenv("TOY_FAULT", "nan"); monkeypatch.setenv("TOY_FAULT_MOD", "1")
    cp = camp(ledger, make_contract(), tmp_path, batch=1)
    assert cp.step().endswith("failed")
    assert "non-finite" in ledger.trials()[0]["error"] and ledger.trials()[0]["metrics"] is None


def test_recover_after_simulated_crash(ledger, make_contract, tmp_path):
    c = make_contract()
    cp = camp(ledger, c, tmp_path, budget_evals=10)
    cp._refill()
    stuck = ledger.claim(60, "toy", "random")                  # a runner that "died" mid-trial
    cp2 = camp(ledger, c, tmp_path, budget_evals=10)
    assert cp2.recover() == [stuck["id"]]
    cp2.run()
    assert ledger.get(stuck["id"])["state"] == "done" and ledger.get(stuck["id"])["attempt"] == 2


@pytest.mark.slow
def test_chaos_kill9_the_daemon_repeatedly(tmp_path, make_contract):
    """Kill -9 the whole campaign process at random moments; it must resume and finish with an intact ledger."""
    make_contract(timeout=20)
    wd = tmp_path / "chaos"
    cmd = [sys.executable, "-m", "antslab.cli", "run", "--contract", str(tmp_path / "task.toml"),
           "--workdir", str(wd), "--arm", "evo", "--seed", "3", "--max-evals", "40"]
    env = {**os.environ, "TOY_SLEEP": "0.15"}
    rnd = random.Random(1)
    kills = 0
    for _ in range(40):
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            p.wait(timeout=rnd.uniform(0.6, 2.0))
            break                                              # finished by itself
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGKILL); p.wait(); kills += 1
    else:
        pytest.fail("never finished")
    assert kills >= 2
    from antslab.ledger import Ledger
    l = Ledger(wd / "ledger.sqlite")
    ok, bad = l.verify_chain()
    assert ok, bad
    rows = l.trials("toy", "evo")
    assert not [t for t in rows if t["state"] == "running"]
    done = [t for t in rows if t["state"] in ("done", "failed", "timeout", "crashed")]
    assert len(done) >= 40
    keys = [(t["spec_key"], t["stage"]) for t in rows if t["state"] == "done"]
    assert len(keys) == len(set(keys))                         # no evaluation was duplicated
    assert any(e["kind"] in ("recovered", "trial_reclaimed") for e in l.events())


def test_static_cost_prefilter_skips_certain_cap_violations(ledger, make_contract, tmp_path):
    """Over-cap proposals cost no CPU except a small stepping-stone quota; every trial is stamped with the epoch."""
    import dataclasses
    c = make_contract()
    c = dataclasses.replace(c, static_cost="tests.test_campaign:_n_params", stone_quota=0.0, caps=[100, 150])
    cp = camp(ledger, c, tmp_path, "random", budget_evals=20)
    cp.p.accept = None          # the random arm normally samples the feasible region itself; here exercise the pre-filter in _refill
    cp.run()
    rows = ledger.trials("toy", "random")
    assert rows and all(_n_params(t["params"]) <= 150 for t in rows)
    assert cp.stats["static_rejected"] > 0
    assert {t["epoch"] for t in rows} == {cp.epoch} and ledger.conn.execute("SELECT COUNT(*) FROM epochs").fetchone()[0] == 1
    fin = ledger.events("trial_finished")[-1]["payload"]
    assert fin["epoch"] == cp.epoch and "edit" in fin and "parent_id" in fin and "cpu_s" in fin


def _n_params(spec):
    return spec["b"] * 10 + (20 if spec["a"] > 0.5 else 0)


def test_history_ignores_other_epochs(ledger, make_contract):
    from antslab.history import History
    c = make_contract()
    for i, ep in enumerate(("e1", "e2")):
        ledger.enqueue("toy", "h", i, {"a": .1 * i, "b": 3, "c": "u"}, "x", spec_key=f"k{i}", epoch=ep)
        t = ledger.claim(60, arm="h"); ledger.finish(t["id"], 1, "done", {"val_mse": 1.0 + i, "n_params": 30})
    assert History(ledger, c, "h", epoch="e1").update() == 1 and History(ledger, c, "h").update() == 2


def test_history_memory_is_bounded(ledger, make_contract):
    from antslab.history import History
    c = make_contract()
    h = History(ledger, c, "h", max_records=50)
    for i in range(300):
        ledger.enqueue("toy", "h", i, {"a": (i % 97) / 97, "b": 1 + i % 30, "c": "u"}, "x", spec_key=f"k{i}")
        t = ledger.claim(60, arm="h"); ledger.finish(t["id"], 1, "done", {"val_mse": 1.0 + (i % 13) * .1, "n_params": 30})
        h.update()
    assert len(h.order) <= 60 and len(h.first) < 300 and h.n == 300


@pytest.mark.slow
def test_portfolio_is_not_worse_than_its_best_member():
    """Paper A's core claim, stored as a regression test (ideas.md finding 1).

    The meta-controller used to be ~1.5x worse than qd alone on `trap`: the bandit's myopic cell-fill success rate
    starves the one operator (qd) that implements stepping-stone search, while sweep burns ~35% of the budget
    delivering almost nothing. The fix is a discounted Beta + a reserved half for the evo/qd pair with the noisy
    operators dropped. Tolerance: portfolio mean regret <= best single operator * 1.15 at every checkpoint.
    """
    import statistics
    from antslab.bench import drive, make_contract, trap, OPTIMUM
    from antslab.portfolio import build_portfolio, PortfolioProposer, RandomProposer, EvoProposer, QDProposer
    from antslab.contract import load_static_cost

    c, static = make_contract(), load_static_cost(make_contract().static_cost)
    fn = lambda s, n, r: trap(s)
    truth = lambda s: trap(s)
    seeds, evals, checkpoints = range(3), 250, (100, 250)

    def single(name):
        def f(cc, seed):
            rng = lambda i: random.Random(seed * 101 + i)
            m = {"random": RandomProposer(cc, rng(1)), "evo": EvoProposer(cc, rng(3)), "qd": QDProposer(cc, rng(10))}[name]
            return PortfolioProposer(cc, {name: m}, rng(0))
        return f

    def portfolio(cc, seed):
        return build_portfolio(cc, seed, with_tpe=False)

    def mean_regret(fac, ck):
        return statistics.mean(drive(fac, c, evals, s, noise=0.01, fn=fn, truth=truth, static=static,
                                     checkpoints=checkpoints)["best"][ck] - OPTIMUM for s in seeds)

    best_single = min(mean_regret(single(n), ck) for n in ("random", "evo", "qd") for ck in checkpoints)
    port = mean_regret(portfolio, evals)
    assert port <= best_single * 1.15, f"portfolio {port:.4f} > best single {best_single:.4f} x 1.15"
