import random
from antslab import bench
from antslab.contract import load_static_cost
from antslab.portfolio import PerCapEvoProposer
from pathlib import Path

CT = Path(__file__).resolve().parents[1] / "contracts" / "tinyts_synth_hard.toml"


def test_percap_evo_covers_every_cap_bin_and_respects_bin_edges():
    from antslab.contract import load_contract
    c = load_contract(CT)
    static = load_static_cost(c.static_cost)
    r = bench.drive(lambda cc, s: PerCapEvoProposer(cc, random.Random(s)), c, 120, 0, noise=0.0, batch=4, checkpoints=(120,),
                    fn=lambda spec, n=0, rg=None: {"rel_mse": 0.5 + 0.1 * random.Random(hash(str(spec)) % 1000).random(), "n_params": static(spec)})
    r["hist"].update()
    caps = sorted(c.caps)
    bins = {}
    for rec in r["hist"].first.values():
        bins.setdefault(r["hist"].cap_bin(rec["metrics"]), 0)
        bins[r["hist"].cap_bin(rec["metrics"])] += 1
    assert set(caps) <= set(bins)                              # a separate search happens in EVERY bin
    assert max(bins.values()) <= 0.5 * 120                     # and the budget is spread, not dumped in the largest bin

def test_percap_pool_filter_has_no_dead_stage_condition(ledger, make_contract):
    """Regression: the evo_percap parent pool filter had a dead condition
    ``(lo < m <= hi and stage_idx == 0) or (lo < m <= hi)`` which is equivalent
    to ``lo < m <= hi`` — the stage_idx check never excluded anything. The
    simplified form includes best records at any stage, which is correct:
    deeper-stage records are better validated and should be eligible parents."""
    from antslab.history import History
    from antslab.portfolio import PerCapEvoProposer
    import random
    c = make_contract()
    h = History(ledger, c, "h")
    for i in range(6):
        ledger.enqueue("toy", "h", i, {"a": i / 6, "b": 5, "c": "u"}, "evo", spec_key=f"k{i}")
        t = ledger.claim(60, arm="h")
        ledger.finish(t["id"], 1, "done", {"val_mse": 1.0 + i * 0.1, "n_params": 50})
    h.update()
    pf = PerCapEvoProposer(c, random.Random(42))
    out = pf.propose(ledger, "h", 6, set(), hist=h)
    assert len(out) == 6                                        # enough parents in the bin → full batch
