#!/usr/bin/env python3
"""How much does the regularisation strength matter once everything else is fixed? (roadmap step 46, question from SearchCast)

    python scripts/exp_ridge_path.py LEDGER.sqlite [LEDGER.sqlite ...] OUT.jsonl [--per-group 6] [--workers 6]

For specifications taken from finished second-stage trials (the plateau at the top, a random sample of the top quarter, a random sample of the
second quarter), the ridge lever is replaced by each value of a grid (13 values from 1e-3 to 1e3, plus 1e-6, which is ordinary least squares in
practice) and the specification is run through s1 again with everything else unchanged. Output: one JSON line per (specification, ridge).
`exp_ridge_path_report.py` reads it. The evaluator is the repository's gym; this changes no repository file.
"""
import argparse
import json
import random
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
GRID = [1e-6] + [10 ** (-3 + 0.5 * i) for i in range(13)]


def one(job):
    from antslab.gyms.tinyts import evaluate_stage_catalog
    spec, ridge = dict(job["spec"]), job["ridge"]
    spec["ridge"] = ridge
    t0 = time.time()
    try:
        m = evaluate_stage_catalog(spec, "s1")
        return {**{k: job[k] for k in ("id", "group", "ledger", "ridge")}, "rel_mse": m.get("rel_mse"), "n_params": m.get("n_params"), "wall_s": time.time() - t0}
    except Exception as e:
        return {**{k: job[k] for k in ("id", "group", "ledger", "ridge")}, "error": f"{type(e).__name__}: {str(e)[:100]}", "wall_s": time.time() - t0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+"); ap.add_argument("--per-group", type=int, default=6); ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    *ledgers, out = a.paths
    rng = random.Random(a.seed)
    jobs, meta = [], {}
    for lp in ledgers:
        db = sqlite3.connect(f"file:{lp}?mode=ro", uri=True)
        rows = [(json.loads(m)["rel_mse"], k, json.loads(p)) for k, p, m in db.execute("select spec_key, params, metrics from trials where stage='s1' and state='done'")]
        rows.sort(key=lambda r: r[0])
        n = len(rows)
        groups = {"top": rows[:max(1, n // 100)], "q1": rows[n // 100:n // 4], "q2": rows[n // 4:n // 2]}
        for g, rs in groups.items():
            for v, k, spec in rng.sample(rs, min(a.per_group, len(rs))):
                tag = f"{Path(lp).stem}:{k}"
                meta[tag] = {"group": g, "own_ridge": spec.get("ridge"), "own_s1": v}
                for r in GRID + [spec["ridge"]]:
                    jobs.append({"id": tag, "group": g, "ledger": Path(lp).stem, "spec": spec, "ridge": r})
    done = set()
    if Path(out).exists():
        done = {(json.loads(l)["id"], json.loads(l)["ridge"]) for l in open(out)}
    todo = [j for j in jobs if (j["id"], j["ridge"]) not in done]
    print(f"{len(meta)} specifications, {len(jobs)} runs, {len(todo)} to do on {a.workers} workers", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex, open(out, "a") as f:
        futs = [ex.submit(one, j) for j in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            r.update({"own_ridge": meta[r["id"]]["own_ridge"], "own_s1": meta[r["id"]]["own_s1"]})
            f.write(json.dumps(r) + "\n"); f.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(todo)} {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
