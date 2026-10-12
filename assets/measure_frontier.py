#!/usr/bin/env python3
"""Measure the reference points of the frontier figure under EXACTLY the protocol the search uses at stage S2
(same datasets, folds, window caps), and read the best specs per cap bin from a ledger at their deepest stage.

    python assets/measure_frontier.py --workdir runs/demo --contract contracts/tinyts_real.toml
    python assets/make_figures.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="runs/demo")
    ap.add_argument("--contract", default="contracts/tinyts_real.toml")
    ap.add_argument("--arm", default="portfolio")
    ap.add_argument("--stage", default="s2")
    ap.add_argument("--out", default=str(ROOT / "assets" / "frontier.json"))
    a = ap.parse_args()
    from antslab.contract import load_contract
    from antslab.gyms import tinyts as T
    from antslab.history import History
    from antslab.ledger import Ledger
    from antslab.ui import leaderboard

    B = T.baseline_specs()
    specs = {"fits_like": B["fits_like"], "dlinear_like": B["dlinear_like"]}
    for L in (96, 336, 720):
        specs[f"ridge_L{L}"] = {**B["dlinear_like"], "lookback": L, "decomp": "none", "norm": "last", "ridge": 1.0}
    measured = {}
    for k, s in specs.items():
        r = T.evaluate_stage_catalog(s, a.stage)
        measured[k] = {"params": T.count_params(s), "rel_mse": r["rel_mse"]}
        print(f"{k:14s} params={measured[k]['params']:>8d}  rel_mse={measured[k]['rel_mse']:.4f}", file=sys.stderr, flush=True)

    c = load_contract(Path(a.contract))
    led = Ledger(Path(a.workdir) / "ledger.sqlite")
    lb = leaderboard(History(led, c, a.arm), c, top=1)
    n_trials = led.conn.execute("SELECT COUNT(*) FROM trials WHERE gym=? AND arm=? AND state='done'", (c.gym, a.arm)).fetchone()[0]
    best = [{"cap": float(cap), "params": rows[0]["n_params"], "rel_mse": rows[0]["value"], "stage": rows[0]["stage"]}
            for cap, rows in lb.items() if rows]
    out = {"protocol": f"stage {a.stage} of {a.contract}: rolling-origin validation, closed-form forecasters; rel_mse = family-weighted geometric mean of MSE / MSE(seasonal naive)",
           "measured": measured, "ants_demo": {"finished_trials": n_trials, "best_by_bin": best}}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print("wrote", a.out, file=sys.stderr)


if __name__ == "__main__":
    main()
