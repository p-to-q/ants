#!/usr/bin/env python3
"""Roadmap step 42: send an UNSELECTED sample of specifications through the second stage and compare it with the first.

    python scripts/exp_unselected_s1.py LEDGER.sqlite OUT.jsonl [--n 300] [--workers 6] [--seed 0]

The promotion rule only ever shows the second stage the top quarter (per size cap) of the first, so the agreement between the two stages
measured on promoted trials is range-restricted. Here the specifications are drawn uniformly at random from ALL finished first-stage
trials of the ledger that never reached the second stage, run through the second stage in this process pool, and the pairs are written
as JSON lines (spec_key, n_params, s0 value, s1 metrics). `exp_unselected_s1_report.py` reads the file. The evaluator is the repository's
own gym; it must be the one whose fingerprint the ledger's epoch records, or the pairs mix two measurements (checked below).
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


def one(item):
    from antslab.gyms.tinyts import evaluate_stage_catalog
    t0 = time.time()
    try:
        m = evaluate_stage_catalog(item["params"], "s1")
        return {**{k: item[k] for k in ("spec_key", "s0", "n_params", "source")}, "s1": m, "wall_s": time.time() - t0}
    except Exception as e:                                                      # a failed evaluation is a result too
        return {**{k: item[k] for k in ("spec_key", "s0", "n_params", "source")}, "error": f"{type(e).__name__}: {str(e)[:120]}", "wall_s": time.time() - t0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger"); ap.add_argument("out")
    ap.add_argument("--n", type=int, default=300); ap.add_argument("--workers", type=int, default=6); ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    from antslab.contract import load_contract, code_fingerprint
    root = Path(__file__).resolve().parents[1]
    c = load_contract(str(root / "contracts" / "tinyts_real.toml"))
    fp = code_fingerprint(c.code_paths)[:16]
    db = sqlite3.connect(f"file:{a.ledger}?mode=ro", uri=True)
    ep = db.execute("select * from epochs order by 2 limit 1").fetchone()
    info = json.loads(ep[2]) if ep else {}
    if info.get("code") and info["code"] != fp:
        sys.exit(f"the ledger's epoch was measured by other evaluator code ({info['code']} against {fp}); refusing to mix")

    reached = {r[0] for r in db.execute("select spec_key from trials where stage='s1' and state='done'")}
    pool = []
    for key, params, metrics, src in db.execute("select spec_key, params, metrics, source from trials where stage='s0' and state='done'"):
        m = json.loads(metrics)
        if key not in reached and "rel_mse" in m and "n_params" in m:
            pool.append({"spec_key": key, "params": json.loads(params), "s0": m["rel_mse"], "n_params": m["n_params"], "source": src})
    random.Random(a.seed).shuffle(pool)
    sample = pool[:a.n]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["spec_key"] for l in open(a.out)}
    todo = [s for s in sample if s["spec_key"] not in done]
    print(f"{len(pool)} candidates that never reached s1; sample {len(sample)}; {len(todo)} to run on {a.workers} workers", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex, open(a.out, "a") as f:
        futs = [ex.submit(one, s) for s in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            f.write(json.dumps(fu.result()) + "\n"); f.flush()
            if i % 10 == 0:
                print(f"  {i}/{len(todo)} done, {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
