import math
from antslab import bench
from antslab.history import History
from antslab.landscape import diagnostics, niche_table
from antslab.lineage import stepping_stats
from antslab.portfolio import build_portfolio


def test_trajectory_velocity_and_acceleration_have_the_right_signs(ledger, make_contract):
    c = make_contract()
    h = History(ledger, c, "t")
    h.traj[("k",)] = [10.0] * 20 + [9.0 - 0.1 * i for i in range(30)]          # flat, then steady descent
    t = h.trajectory(("k",), w=10)
    assert t["velocity"] > 0 and t["n"] == 50
    h.traj[("acc",)] = [10.0] * 20 + [10.0 - 0.01 * i * i for i in range(30)]    # improving faster and faster
    h.traj[("dec",)] = [10.0] * 20 + [10.0 - 4.0 * (1 - math.exp(-i / 4)) for i in range(30)]   # saturating
    assert h.trajectory(("acc",), w=10)["acceleration"] > 0 > h.trajectory(("dec",), w=10)["acceleration"]
    assert h.potential(("young",)) == 1.0 and h.potential(("dec",)) < h.potential(("acc",))


def test_stepping_stats_empty_and_all_infeasible(ledger, make_contract):
    """stepping_stats must not crash on a fresh arm (no trials) or an arm where
    every first-stage record is over cap — both are reachable because board.build
    and landscape.diagnostics call it unconditionally."""
    c = make_contract()
    # Empty history: no trials at all.
    h = History(ledger, c, "empty")
    h.update()
    s = stepping_stats(h)
    assert s["n_evals"] == 0 and s["champions"] == 0 and s["lead_changed"] is False
    assert s["non_leader_ancestor_frac"] is None and s["worst_ancestor_pct"] is None

    # All infeasible: every trial over the cap (n_params > 300).
    h2 = History(ledger, c, "infeasible")
    for i in range(4):
        ledger.enqueue("toy", "infeasible", i, {"a": 0.5, "b": 5, "c": "u"}, "random", spec_key=f"inf{i}")
        t = ledger.claim(60, arm="infeasible")
        ledger.finish(t["id"], 1, "done", {"val_mse": float(i), "n_params": 5000})
    h2.update()
    s2 = stepping_stats(h2)
    assert s2["n_evals"] == 4 and s2["champions"] == 0 and s2["lead_changed"] is False


def test_leader_and_riser_are_different_things():
    c = bench.make_contract()
    r = bench.drive(lambda cc, s: build_portfolio(cc, s, with_tpe=False), c, 400, 3, checkpoints=(400,))
    rows = niche_table(r["hist"])
    assert rows[0]["label"] in ("leader", "young") and {x["label"] for x in rows} <= {"leader", "riser", "stalled", "young"}
    r["hist"].update()                                   # drive() stops mid-batch; ingest the tail before asserting
    d = diagnostics(r["hist"])
    assert d["evaluations"] >= 400 and d["ruggedness"] is not None and "stepping_stones" in d
