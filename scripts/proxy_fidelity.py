#!/usr/bin/env python3
"""How well does a cheap stage predict a deeper one? Evaluate N random FEASIBLE specs at every stage and report rank correlations
and top-quartile agreement. The cascade promotes by the cheap stage, so this number decides whether the cascade is a filter or a lottery.

    python scripts/proxy_fidelity.py --n 48 --seed 1 --out assets/proxy_fidelity.json
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va and vb else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", default=str(ROOT / "contracts" / "tinyts_real.toml"))
    ap.add_argument("--n", type=int, default=48)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(ROOT / "assets" / "proxy_fidelity.json"))
    a = ap.parse_args()
    from antslab.contract import load_contract
    from antslab.gyms import tinyts as T

    c = load_contract(Path(a.contract))
    rng = random.Random(a.seed)
    cap = max(c.caps)
    specs, tries = [], 0
    while len(specs) < a.n and tries < 2_000_000:
        tries += 1
        s = c.space.sample(rng)
        if T.est_mem_mb(s) <= c.mem_cap_mb and T.count_params(s) <= cap:
            specs.append(s)
    print(f"{len(specs)} feasible specs from {tries} draws ({100*len(specs)/tries:.2f}% feasible)", file=sys.stderr, flush=True)
    res = {st: [] for st in c.stages}
    t0 = time.time()
    for i, s in enumerate(specs):
        for st in c.stages:
            res[st].append(T.evaluate_stage_catalog(s, st)["rel_mse"])
        print(f"{i+1}/{len(specs)} {time.time()-t0:.0f}s  " + "  ".join(f"{st}={res[st][-1]:.4f}" for st in c.stages), file=sys.stderr, flush=True)
    out = {"n": len(specs), "seed": a.seed, "draws": tries, "values": res, "spearman": {}, "top_quartile_overlap": {}}
    stages = list(c.stages)
    for i, x in enumerate(stages):
        for y in stages[i + 1:]:
            out["spearman"][f"{x}~{y}"] = spearman(res[x], res[y])
            k = max(1, len(specs) // 4)
            top = lambda st: set(sorted(range(len(specs)), key=lambda j: res[st][j])[:k])
            out["top_quartile_overlap"][f"{x}~{y}"] = len(top(x) & top(y)) / k
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("n", "spearman", "top_quartile_overlap")}, indent=1))


if __name__ == "__main__":
    main()
