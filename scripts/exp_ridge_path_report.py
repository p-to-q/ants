#!/usr/bin/env python3
"""Reads the output of exp_ridge_path.py.

    python scripts/exp_ridge_path_report.py OUT.jsonl

Per group (top plateau, top quarter, second quarter): how much lower the second-stage rel_mse is at the best grid value than (a) at the
specification's own ridge as the search chose it, (b) at ridge = 1e-6 (ordinary least squares in practice), and how often the best grid value
differs from the searched one by more than half a decade.
"""
import json
import sys
from collections import defaultdict

import numpy as np


def main():
    rows = [json.loads(l) for l in open(sys.argv[1])]
    ok = [r for r in rows if r.get("rel_mse") is not None]
    print(f"{len(rows)} runs, {len(ok)} with a value, {len(rows) - len(ok)} failed")
    by = defaultdict(dict)
    for r in ok:
        by[r["id"]][r["ridge"]] = r["rel_mse"]
    meta = {r["id"]: r for r in ok}
    res = defaultdict(list)
    for sid, d in by.items():
        own_ridge, own = meta[sid]["own_ridge"], d.get(meta[sid]["own_ridge"])
        grid = {k: v for k, v in d.items() if k != own_ridge}
        if own is None or not grid:
            continue
        best_r, best = min(grid.items(), key=lambda kv: kv[1])
        best_r, best = (own_ridge, own) if own <= best else (best_r, best)
        ols = d.get(1e-6)
        res[meta[sid]["group"]].append({"gain_own": (own - best) / own, "gain_ols": ((ols - best) / ols) if ols else np.nan,
                                        "shift_dec": abs(np.log10(best_r) - np.log10(own_ridge)), "own": own, "best": best, "ols": ols})
    print(f"{'group':6s} {'n':>3s} {'vs own ridge':>14s} {'vs OLS-like (1e-6)':>20s} {'best differs >0.5 decade':>26s}")
    for g in ("top", "q1", "q2"):
        v = res.get(g, [])
        if not v:
            continue
        go, gl = np.array([x["gain_own"] for x in v]), np.array([x["gain_ols"] for x in v])
        print(f"{g:6s} {len(v):3d} {go.mean():13.2%} {np.nanmean(gl):19.2%} {np.mean([x['shift_dec'] > 0.5 for x in v]):25.0%}   (median own {np.median([x['own'] for x in v]):.4f}, best {np.median([x['best'] for x in v]):.4f})")


if __name__ == "__main__":
    main()
