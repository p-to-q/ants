#!/usr/bin/env python3
"""Reads the output of exp_unselected_s1.py and answers the step-42 questions.

    python scripts/exp_unselected_s1_report.py OUT.jsonl [LEDGER.sqlite]

1. Spearman correlation of the first stage with the second over an unselected sample (with a bootstrap interval).
2. If the ledger is given, the promotion rule is replayed on the sample: within each size cap the top quarter by the first stage would
   have been sent on; the report says how many of the sample's best second-stage results (top 10% and top 25% of the sample) that rule
   discards, and what the second-stage value of the discarded ones is.
"""
import json
import sys

import numpy as np
from scipy.stats import spearmanr

CAPS = (100, 300, 1000, 3000)


def cap_of(n):
    return next((c for c in CAPS if n <= c), None)


def main():
    rows = [json.loads(l) for l in open(sys.argv[1])]
    ok = [r for r in rows if "s1" in r and "rel_mse" in r["s1"]]
    bad = [r for r in rows if "s1" not in r or "rel_mse" not in r.get("s1", {})]
    print(f"{len(rows)} evaluated, {len(ok)} with a second-stage value, {len(bad)} failed ({sorted({r.get('error', '?')[:40] for r in bad})[:3]})")
    s0 = np.array([r["s0"] for r in ok]); s1 = np.array([r["s1"]["rel_mse"] for r in ok]); npar = np.array([r["n_params"] for r in ok])
    rho = spearmanr(s0, s1)[0]
    rng = np.random.default_rng(0)
    bs = [spearmanr(s0[i], s1[i])[0] for i in (rng.integers(0, len(s0), len(s0)) for _ in range(500))]
    print(f"Spearman s0-s1 over the unselected sample: {rho:.3f}  (95% bootstrap interval {np.percentile(bs, 2.5):.3f} to {np.percentile(bs, 97.5):.3f}, n={len(s0)})")
    print(f"  s1 quantiles 10/25/50/75/90%: {[round(float(np.percentile(s1, p)), 3) for p in (10, 25, 50, 75, 90)]}")
    caps = np.array([cap_of(n) or 99999 for n in npar])
    promoted = np.zeros(len(s0), bool)
    for c in sorted(set(caps)):
        idx = np.where(caps == c)[0]
        k = max(1, int(round(len(idx) * 0.25)))
        promoted[idx[np.argsort(s0[idx])[:k]]] = True
    print(f"promotion replay (top quarter of s0 within each cap sends on {promoted.sum()} of {len(s0)}):")
    for q in (10, 25):
        thr = np.percentile(s1, q)
        top = s1 <= thr
        lost = top & ~promoted
        print(f"  of the sample's best {q}% at s1 ({top.sum()}), the rule would have discarded {lost.sum()} ({lost.sum() / max(top.sum(), 1):.0%})")
    print(f"  median s1 of promoted {np.median(s1[promoted]):.3f}, of discarded {np.median(s1[~promoted]):.3f}")
    by = {}
    for r in ok:
        by.setdefault(r["source"].split(":")[0], []).append(r["s1"]["rel_mse"])
    print("  s1 median by proposing operator (this sample):", {k: (len(v), round(float(np.median(v)), 3)) for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))})


if __name__ == "__main__":
    main()
