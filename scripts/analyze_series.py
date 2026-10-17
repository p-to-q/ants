#!/usr/bin/env python3
"""The pre-registered analysis of a series of runs (docs/experiments/E43-learned-screen.md, section 4).

    python scripts/analyze_series.py BASE [--out report.md] [--csv runs.csv]
           [--control portfolio] [--treatment portfolio_screen] [--budget 50400] [--cap 300]

BASE holds one workdir per run, named <arm>_s<seed> (what `ants seeds` / `ants series run` write). Ledgers are opened read-only. Run it once,
when every run has finished. Needs numpy and scipy (analysis, not dependencies of ants).

Definitions (section 3 of the protocol): the *value at budget B* of a run and a cap is the minimum rel_mse over finished deepest-stage
trials with n_params <= cap that finished before the run's cumulative CPU (sum of cpu_s of all finished trials, in finish order) exceeded B.
"""
import argparse
import csv
import itertools
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
CAPS = (100, 300, 1000, 3000)
NAME = re.compile(r"^(?P<arm>[a-z0-9_]+)_s(?P<seed>\d+)$")


def load_run(wd: Path) -> dict:
    p = wd / "ledger.sqlite"
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT stage, metrics, cpu_s, epoch FROM trials WHERE state='done' ORDER BY finished_at, id").fetchall()
    stages = [r[0] for r in c.execute("SELECT DISTINCT stage FROM trials")]
    deepest = max(stages, key=lambda s: (len(s), s)) if stages else None             # s0 < s1 < s2 (the contract's order; names sort)
    cum, s2, cpu_by_stage, epochs = 0.0, [], defaultdict(float), set()
    for r in rows:
        cum += r["cpu_s"] or 0.0
        cpu_by_stage[r["stage"]] += r["cpu_s"] or 0.0
        epochs.add(r["epoch"])
        if r["stage"] == deepest:
            m = json.loads(r["metrics"] or "{}")
            if "rel_mse" in m and "n_params" in m:
                s2.append((cum, m["rel_mse"], m["n_params"]))
    hashes, starts = set(), 0
    for (pl,) in c.execute("SELECT payload FROM events WHERE kind='daemon_started'"):
        starts += 1
        hashes.add(json.loads(pl).get("arm_def_hash"))
    chain_ok = None
    try:
        from antslab.ledger import Ledger
        chain_ok = Ledger.open_readonly(p).verify_chain()[0]
    except Exception as e:                                                          # a ledger this ants cannot read is reported, not hidden
        chain_ok = f"unreadable: {type(e).__name__}"
    return {"s2": s2, "cum": cum, "cpu_by_stage": dict(cpu_by_stage), "epochs": sorted(e for e in epochs if e), "hashes": sorted(h for h in hashes if h),
            "starts": starts, "chain_ok": chain_ok, "trials": len(rows)}


def value_at(run, cap, budget):
    v = [rm for cum, rm, n in run["s2"] if n <= cap and cum <= budget]
    return min(v) if v else float("nan")


def curve(run, cap, grid):
    return [value_at(run, cap, b) for b in grid]


def auc(run, cap, budget, steps=48):
    """Mean of the best-so-far curve over the budget, with the reference (1.0) before the first deepest-stage result."""
    g = np.linspace(budget / steps, budget, steps)
    v = np.array(curve(run, cap, g))
    return float(np.mean(np.where(np.isnan(v), 1.0, np.minimum(v, 1.0))))


def time_to(run, cap, target, budget):
    t = [cum for cum, rm, n in run["s2"] if n <= cap and cum <= budget and rm <= target]
    return min(t) if t else float("nan")


def paired(ctrl, trt, seeds):
    a = np.array([ctrl[s] for s in seeds if s in ctrl and s in trt and not (np.isnan(ctrl[s]) or np.isnan(trt[s]))])
    b = np.array([trt[s] for s in seeds if s in ctrl and s in trt and not (np.isnan(ctrl[s]) or np.isnan(trt[s]))])
    return a, b


def summary(a, b, rng):
    d = b - a                                                                       # treatment minus control; negative is better
    out = {"n": len(d)}
    if len(d) < 3:
        return out
    out["median_diff"] = float(np.median(d))
    bs = [np.median(d[rng.integers(0, len(d), len(d))]) for _ in range(10000)]
    out["ci"] = (float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)))
    try:
        out["wilcoxon_p"] = float(stats.wilcoxon(b, a, method="exact" if len(d) <= 25 else "approx").pvalue)
    except ValueError:
        out["wilcoxon_p"] = float("nan")
    out["mannwhitney_p"] = float(stats.mannwhitneyu(b, a, alternative="two-sided").pvalue)
    return out


def verdict(s):
    if s.get("n", 0) < 3:
        return "too few pairs"
    if s["wilcoxon_p"] < 0.05 and s["median_diff"] <= -0.005:
        return "evidence for H43a (p < 0.05 and median difference <= -0.005)"
    if -0.01 <= s["ci"][0] and s["ci"][1] <= 0.01:
        return "no effect (interval inside +-0.01)"
    return "inconclusive"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("--out"); ap.add_argument("--csv")
    ap.add_argument("--control", default="portfolio"); ap.add_argument("--treatment", default="portfolio_screen")
    ap.add_argument("--budget", type=float, default=50400); ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--epoch", default="5da6eeee6527")
    a = ap.parse_args()
    base = Path(a.base)
    runs = {}
    for wd in sorted(base.iterdir()):
        m = NAME.match(wd.name)
        if m and (wd / "ledger.sqlite").exists():
            runs[(m["arm"], int(m["seed"]))] = load_run(wd)
    arms = sorted({k[0] for k in runs})
    seeds = sorted({k[1] for k in runs})
    rng = np.random.default_rng(20261017)
    L = []
    P = L.append
    P(f"# Series report: {base}\n")
    P(f"{len(runs)} runs; arms {arms}; seeds {seeds}; budget {a.budget:.0f} CPU-s; control `{a.control}`, treatment `{a.treatment}`.\n")

    P("## Validity checks\n")
    P("| run | epoch ok | definition hashes | starts | chain | CPU-s | deepest-stage trials | reached budget |")
    P("|---|---|---|---|---|---|---|---|")
    for (arm, s), r in sorted(runs.items()):
        P(f"| {arm}_s{s} | {r['epochs'] == [a.epoch]} | {len(r['hashes'])} | {r['starts']} | {r['chain_ok']} | {r['cum']:.0f} | {len(r['s2'])} | {r['cum'] >= a.budget} |")

    P("\n## Endpoints at the budget (median over seeds; best deepest-stage rel_mse among models of at most the cap)\n")
    P("| arm | " + " | ".join(f"<= {c}" for c in CAPS) + " | s1 share of CPU | P(rel_mse <= 0.80 at 3000) |")
    P("|---|" + "---|" * (len(CAPS) + 2))
    vals = {}
    for arm in arms:
        row = []
        for c in CAPS:
            v = {s: value_at(runs[(arm, s)], c, a.budget) for s in seeds if (arm, s) in runs}
            vals[(arm, c)] = v
            x = [t for t in v.values() if not np.isnan(t)]
            row.append(f"{np.median(x):.4f} ({np.min(x):.4f} to {np.max(x):.4f}, n={len(x)})" if x else "-")
        share = [r["cpu_by_stage"].get("s1", 0) / r["cum"] for (ar, _), r in runs.items() if ar == arm and r["cum"]]
        v3 = [t for t in vals[(arm, 3000)].values() if not np.isnan(t)]
        P(f"| {arm} | " + " | ".join(row) + f" | {np.median(share):.2f} | {np.mean([t <= 0.80 for t in v3]):.2f} |" if v3 else f"| {arm} | " + " | ".join(row) + " | - | - |")

    if a.control in arms and a.treatment in arms:
        P(f"\n## Primary: cap {a.cap}, {a.treatment} minus {a.control} (negative is better)\n")
        ca = {s: value_at(runs[(a.control, s)], a.cap, a.budget) for s in seeds if (a.control, s) in runs}
        tr = {s: value_at(runs[(a.treatment, s)], a.cap, a.budget) for s in seeds if (a.treatment, s) in runs}
        x, y = paired(ca, tr, seeds)
        s = summary(x, y, rng)
        P(f"pairs {s['n']}; " + (f"median difference {s['median_diff']:+.4f}, bootstrap 95% interval {s['ci'][0]:+.4f} to {s['ci'][1]:+.4f}; Wilcoxon signed-rank p = {s['wilcoxon_p']:.4f}; "
                                 f"Mann-Whitney p = {s['mannwhitney_p']:.4f}.\n\n**Reading: {verdict(s)}.**" if s["n"] >= 3 else "too few pairs to test."))
        P("\n## Secondary (exploratory; intervals, not confirmations)\n")
        P("| endpoint | pairs | median diff | 95% interval | Wilcoxon p |")
        P("|---|---|---|---|---|")
        pooled = {c: np.nanmin([value_at(r, c, a.budget) for r in runs.values()] or [np.nan]) for c in CAPS}
        def row(name, ca_, tr_):
            x, y = paired(ca_, tr_, seeds)
            s = summary(x, y, rng)
            P(f"| {name} | {s['n']} | " + (f"{s['median_diff']:+.4f} | {s['ci'][0]:+.4f} to {s['ci'][1]:+.4f} | {s['wilcoxon_p']:.3f} |" if s["n"] >= 3 else "- | - | - |"))
        for c in CAPS:
            for B in (14400, 28800, a.budget):
                row(f"value at {B:.0f} s, cap {c}", {s_: value_at(runs[(a.control, s_)], c, B) for s_ in seeds if (a.control, s_) in runs},
                    {s_: value_at(runs[(a.treatment, s_)], c, B) for s_ in seeds if (a.treatment, s_) in runs})
            row(f"area under best-so-far, cap {c}", {s_: auc(runs[(a.control, s_)], c, a.budget) for s_ in seeds if (a.control, s_) in runs},
                {s_: auc(runs[(a.treatment, s_)], c, a.budget) for s_ in seeds if (a.treatment, s_) in runs})
            tgt = pooled[c] * 1.03
            row(f"CPU-s to within 3% of the pooled best, cap {c}", {s_: time_to(runs[(a.control, s_)], c, tgt, a.budget) for s_ in seeds if (a.control, s_) in runs},
                {s_: time_to(runs[(a.treatment, s_)], c, tgt, a.budget) for s_ in seeds if (a.treatment, s_) in runs})

    P("\n## Restarts against one long run (roadmap step 44)\n")
    P("Mean over all subsets of K seeds of the best value at budget/K, against the mean value of one seed at the full budget.\n")
    P("| arm | cap | one run, full budget | K=2 | K=4 | K=8 |")
    P("|---|---|---|---|---|---|")
    for arm in arms:
        ss = [s for s in seeds if (arm, s) in runs]
        for c in CAPS:
            one = np.nanmean([value_at(runs[(arm, s)], c, a.budget) for s in ss]) if ss else np.nan
            cells = []
            for K in (2, 4, 8):
                if len(ss) < K:
                    cells.append("-"); continue
                m = [np.nanmin([value_at(runs[(arm, s)], c, a.budget / K) for s in sub]) for sub in itertools.combinations(ss, K)]
                cells.append(f"{np.mean(m):.4f}")
            P(f"| {arm} | {c} | {one:.4f} | " + " | ".join(cells) + " |")

    text = "\n".join(L) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["arm", "seed"] + [f"value_{c}_{int(a.budget)}" for c in CAPS] + ["cpu_s", "s1_cpu_share", "chain_ok", "starts"])
            for (arm, s), r in sorted(runs.items()):
                w.writerow([arm, s] + [value_at(r, c, a.budget) for c in CAPS] + [round(r["cum"]), round(r["cpu_by_stage"].get("s1", 0) / r["cum"], 4) if r["cum"] else "", r["chain_ok"], r["starts"]])


if __name__ == "__main__":
    main()
