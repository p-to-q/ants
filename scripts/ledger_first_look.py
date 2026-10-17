#!/usr/bin/env python3
"""First look at one arm's ledger: where the CPU goes, how well the stages agree, which operators reach the top, how many independent
lineages the top comes from, and how well a surrogate fitted on earlier trials ranks later ones.

    python scripts/ledger_first_look.py path/to/ledger.sqlite [--epoch ID]

Read-only (the file is opened with mode=ro; analyse a copy of a live ledger made with `ants backup` or sqlite's backup API, never the live file).
Needs numpy, scipy and scikit-learn (not dependencies of ants; this is analysis, roadmap step 21).
Every number it prints is a function of the ledger, so a reader can recompute it. The surrogate section replays recorded trials: it ranks
candidates that were really proposed and evaluated, so it says nothing about candidates no operator ever proposed (roadmap step 22).
"""
import argparse
import collections
import json
import sqlite3

import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor

CAPS = (100, 300, 1000, 3000)


def load(path, epoch):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    q = "select id,stage,source,spec_key,params,metrics,cpu_s,origin_key,finished_at from trials where state='done'"
    args = ()
    if epoch:
        q += " and epoch=?"
        args = (epoch,)
    rows = c.execute(q + " order by finished_at", args).fetchall()
    by = collections.defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    return rows, by


def val(r, key="rel_mse"):
    return json.loads(r[5]).get(key, np.nan)


def features(params):
    names = sorted({k for p in params for k in p})
    cols, cn = [], []
    for n in names:
        vals = [p.get(n) for p in params]
        cats = sorted({v for v in vals if isinstance(v, str)})
        if cats:
            for l in cats:
                cols.append([1.0 if v == l else 0.0 for v in vals]); cn.append(f"{n}={l}")
        else:
            cols.append([-1.0 if v is None else float(v) for v in vals]); cn.append(n)
    return cn, np.array(cols).T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger")
    ap.add_argument("--epoch")
    a = ap.parse_args()
    rows, by = load(a.ledger, a.epoch)
    print(f"{len(rows)} finished trials")

    print("\n1. where the CPU goes")
    for st in ("s0", "s1", "s2"):
        rs = by.get(st, [])
        if rs:
            cs = sum(r[6] or 0 for r in rs)
            print(f"   {st}: {len(rs)} trials, {cs / 3600:.1f} CPU-h, mean {cs / len(rs):.1f} s")

    print("\n2. how well the stages agree (Spearman over specs that reached both; promotion makes the first two ranges narrow)")
    d = {st: {r[3]: val(r) for r in by.get(st, [])} for st in ("s0", "s1", "s2")}
    for x, y in (("s0", "s1"), ("s1", "s2"), ("s0", "s2")):
        ks = [k for k in d[x] if k in d[y]]
        if len(ks) > 10:
            print(f"   {x}-{y}: n={len(ks)} rho={spearmanr([d[x][k] for k in ks], [d[y][k] for k in ks])[0]:.3f}")
    ks = [k for k in d["s1"] if k in d["s2"]]
    if ks:
        dif = np.array([d["s2"][k] - d["s1"][k] for k in ks])
        print(f"   s2 minus s1: mean {dif.mean():.4f}, sd {dif.std():.4f}")

    print("\n3. time to the best, by cap (cumulative CPU over all stages)")
    cum, best = 0.0, {k: (9.0, 0.0) for k in CAPS}
    hist = {k: [] for k in CAPS}
    for r in rows:
        cum += (r[6] or 0) / 3600
        if r[1] == "s2":
            m = json.loads(r[5])
            for k in CAPS:
                if m.get("n_params", 1e9) <= k and m["rel_mse"] < best[k][0]:
                    best[k] = (m["rel_mse"], cum); hist[k].append((cum, m["rel_mse"]))
    for k in CAPS:
        h = hist[k]
        if h:
            fin = h[-1][1]
            t = lambda f: next(t for t, v in h if v <= fin * f)
            print(f"   <= {k}: best {fin:.4f} at {h[-1][0]:.1f} h; within 3% at {t(1.03):.1f} h, within 1% at {t(1.01):.1f} h (run total {cum:.1f} h)")

    print("\n4. which operators reach the top (by the operator that first proposed the spec)")
    first = {}
    for r in by.get("s0", []):
        first.setdefault(r[3], r[2].split(":")[0])
    s1 = by.get("s1", [])
    if s1:
        v1 = np.array([val(r) for r in s1]); thr = np.percentile(v1, 10)
        dd = collections.defaultdict(list)
        for r, v in zip(s1, v1):
            dd[first.get(r[3], "?")].append(v)
        print("   source  trials-at-s1  median  share-in-top-10%")
        for k, vs in sorted(dd.items(), key=lambda kv: -len(kv[1])):
            vs = np.array(vs); print(f"   {k:7s} {len(vs):6d}  {np.median(vs):.4f}  {np.mean(vs <= thr):.3f}")

    print("\n5. how many independent lineages the top comes from (origin_key chains; only trials that recorded an origin are linked)")
    org = {}
    for r in by.get("s0", []):
        if r[7]:
            org.setdefault(r[3], r[7])
    def root(k):
        seen = {k}
        while k in org and org[k] not in seen:
            k = org[k]; seen.add(k)
        return k
    s2 = sorted(by.get("s2", []), key=lambda r: val(r))[:50]
    if s2:
        roots = collections.Counter(first.get(root(r[3]), "?") + ":" + root(r[3])[:6] for r in s2)
        print(f"   top-{len(s2)} s2: {len(roots)} distinct root(s): {dict(roots)}")

    print("\n6. surrogate replay: fit on the first 70% of finished s1 trials, rank the last 30%")
    if len(s1) > 200:
        cn, X = features([json.loads(r[4]) for r in s1])
        y = np.array([val(r) for r in s1]); cut = int(len(y) * .7)
        rf = RandomForestRegressor(n_estimators=300, min_samples_leaf=3, n_jobs=-1, random_state=0).fit(X[:cut], y[:cut])
        pr, yt = rf.predict(X[cut:]), y[cut:]
        top = yt <= np.percentile(y, 10)
        print(f"   Spearman {spearmanr(pr, yt)[0]:.3f}")
        for keep in (.2, .3, .5):
            sel = np.argsort(pr)[:int(len(pr) * keep)]
            print(f"   evaluating only the best {int(keep * 100)}% by prediction keeps {top[sel].sum() / max(top.sum(), 1):.2f} of the top-10% results")
        order = np.argsort(-rf.feature_importances_)[:8]
        print("   most used features:", [(cn[i], round(float(rf.feature_importances_[i]), 3)) for i in order])


if __name__ == "__main__":
    main()
