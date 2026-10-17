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
    """CPU-seconds until the best-so-far first reaches the target; RIGHT-CENSORED at the budget when it never does (a missing value would favour
    the arm that fails more often)."""
    t = [cum for cum, rm, n in run["s2"] if n <= cap and cum <= budget and rm <= target]
    return min(t) if t else float(budget)


def value_c(run, cap, budget):
    """Value at budget with the reference (rel_mse 1.0 = seasonal naive) for a run that has no deepest-stage result under the cap yet."""
    v = value_at(run, cap, budget)
    return 1.0 if np.isnan(v) else v


def paired(ctrl, trt, seeds):
    a = np.array([ctrl[s] for s in seeds if s in ctrl and s in trt and not (np.isnan(ctrl[s]) or np.isnan(trt[s]))])
    b = np.array([trt[s] for s in seeds if s in ctrl and s in trt and not (np.isnan(ctrl[s]) or np.isnan(trt[s]))])
    return a, b


def _signrank_dist(n):
    """Exact distribution of the Wilcoxon signed-rank statistic T+ for n untied non-zero differences: counts[k] = number of sign patterns with T+ = k."""
    counts = np.zeros(n * (n + 1) // 2 + 1, dtype=np.int64)
    counts[0] = 1
    for r in range(1, n + 1):
        counts[r:] = counts[r:] + counts[:len(counts) - r].copy()
    return counts


def hl_exact(d, alpha=0.05):
    """Hodges-Lehmann estimate (median of the Walsh averages) and the exact distribution-free interval whose coverage is at least 1 - alpha."""
    n = len(d)
    walsh = np.sort([(d[i] + d[j]) / 2 for i in range(n) for j in range(i, n)])
    counts = _signrank_dist(n)
    cdf = np.cumsum(counts) / counts.sum()
    c = int(np.searchsorted(cdf, alpha / 2, side="right")) - 1                       # the largest c with P(T <= c) <= alpha/2 (Hollander and Wolfe)
    c = max(c, 0)
    lo, hi = walsh[c], walsh[len(walsh) - 1 - c]                                     # the (c+1)-th smallest and the (c+1)-th largest Walsh average
    cover = 1 - 2 * float(cdf[c])
    return float(np.median(walsh)), (float(lo), float(hi)), float(cover)


def perm_p(d):
    """Exact sign-flip permutation test of the mean paired difference (two-sided)."""
    n = len(d)
    signs = np.array(list(itertools.product((-1, 1), repeat=n)))
    means = np.abs((signs * np.abs(d)).mean(1))
    return float(np.mean(means >= abs(d.mean()) - 1e-15))


def a12(a, b):
    """Probability that a treatment run (b) beats a control run (a), ties counted half; lower rel_mse is better."""
    return float(np.mean([(y < x) + 0.5 * (y == x) for x in a for y in b]))


def summary(a, b, rng):
    d = b - a                                                                       # treatment minus control; negative is better
    out = {"n": len(d)}
    if len(d) < 3:
        return out
    out["median_diff"] = float(np.median(d))
    out["hl"], out["hl_ci"], out["hl_cover"] = hl_exact(d)
    out["mean_diff"] = float(d.mean())
    out["perm_p"] = perm_p(d) if len(d) <= 16 else float("nan")
    out["a12"] = a12(a, b)
    bs = [a12(a[rng.integers(0, len(a), len(a))], b[rng.integers(0, len(b), len(b))]) for _ in range(2000)]
    out["a12_ci"] = (float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)))
    try:
        out["wilcoxon_p"] = float(stats.wilcoxon(b, a, method="exact" if len(d) <= 25 else "approx").pvalue)
    except ValueError:
        out["wilcoxon_p"] = float("nan")
    out["mannwhitney_p"] = float(stats.mannwhitneyu(b, a, alternative="two-sided").pvalue)
    n = len(d)
    out["sd_d"] = float(np.std(d, ddof=1))
    out["mde80"] = float((stats.t.ppf(1 - 0.0294 / 2, n - 1) + stats.t.ppf(0.8, n - 1)) * out["sd_d"] / np.sqrt(n))     # approximate, per-look level 0.0294
    return out


def verdict(s, alpha):
    if s.get("n", 0) < 3:
        return "too few pairs"
    if s["wilcoxon_p"] < alpha and s["median_diff"] < 0:
        return f"evidence for H43a (exact Wilcoxon p < {alpha})"
    if s["median_diff"] >= 0:
        return "futility at this look: the median paired difference is not in favour of the screen"
    return "inconclusive: the effect, if there is one, is not larger than the minimum detectable effect"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("--out"); ap.add_argument("--csv")
    ap.add_argument("--control", default="portfolio"); ap.add_argument("--treatment", default="portfolio_screen")
    ap.add_argument("--budget", type=float, default=50400); ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--epoch", default="5da6eeee6527")
    ap.add_argument("--alpha", type=float, default=0.0294, help="per-look level of the two-look design (Pocock); 0.05 for a single look")
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
        P(f"Values are censored at the reference (1.0) for a run with no deepest-stage result under the cap; the complete-case result is the first sensitivity row. "
          f"The per-look level is {a.alpha} (two looks, Pocock; see the protocol).\n")

        def pair(fn):
            ca = {s_: fn(runs[(a.control, s_)]) for s_ in seeds if (a.control, s_) in runs}
            tr = {s_: fn(runs[(a.treatment, s_)]) for s_ in seeds if (a.treatment, s_) in runs}
            return paired(ca, tr, seeds)

        def line(name, x, y):
            s_ = summary(x, y, rng)
            if s_["n"] < 3:
                return f"| {name} | {s_['n']} | - | - | - | - | - |"
            return (f"| {name} | {s_['n']} | {s_['median_diff']:+.4f} | {s_['hl']:+.4f} ({s_['hl_ci'][0]:+.4f} to {s_['hl_ci'][1]:+.4f}, coverage {s_['hl_cover']:.3f}) | "
                    f"{s_['wilcoxon_p']:.4f} | {s_['perm_p']:.4f} | {s_['a12']:.2f} ({s_['a12_ci'][0]:.2f} to {s_['a12_ci'][1]:.2f}) |")
        x, y = pair(lambda r: value_c(r, a.cap, a.budget))
        s_ = summary(x, y, rng)
        P("| endpoint | pairs | median diff | Hodges-Lehmann (exact interval) | exact Wilcoxon p | exact permutation p (mean) | A12 (95% interval) |")
        P("|---|---|---|---|---|---|---|")
        P(line("PRIMARY: value at budget, censored", x, y))
        xc, yc = pair(lambda r: value_at(r, a.cap, a.budget))
        P(line("sensitivity: complete cases only", xc, yc))
        for B in (14400, 28800):
            xb, yb = pair(lambda r, B=B: value_c(r, a.cap, B))
            P(line(f"sensitivity: value at {B} s", xb, yb))
        if s_["n"] >= 3:
            P(f"\nMann-Whitney p (unpaired, sensitivity) {s_['mannwhitney_p']:.4f}; sd of the paired differences {s_['sd_d']:.4f}; approximate minimum detectable effect at 80% power {s_['mde80']:.3f}.\n")
            P(f"**Reading: {verdict(s_, a.alpha)}.**")
        P("\n## Secondary (exploratory; intervals, not confirmations; the family has "
          f"{len(CAPS) * 3 + len(CAPS) * 2} rows)\n")
        P("| endpoint | pairs | median diff | Hodges-Lehmann (exact interval) | exact Wilcoxon p | exact permutation p (mean) | A12 (95% interval) |")
        P("|---|---|---|---|---|---|---|")
        pooled = {c: np.nanmin([value_at(r, c, a.budget) for r in runs.values()] or [np.nan]) for c in CAPS}
        for c in CAPS:
            for B in (28800, a.budget):
                x, y = pair(lambda r, c=c, B=B: value_c(r, c, B))
                P(line(f"value at {B:.0f} s, cap {c}", x, y))
            x, y = pair(lambda r, c=c: auc(r, c, a.budget))
            P(line(f"area under best-so-far, cap {c}", x, y))
            tgt = pooled[c] * 1.03
            x, y = pair(lambda r, c=c, tgt=tgt: time_to(r, c, tgt, a.budget))
            P(line(f"CPU-s to within 3% of the pooled best (post hoc target; censored at the budget), cap {c}", x, y))

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
