"""Toy gym (stdlib only) with deterministic noise and opt-in fault injection, used by tests/CI.

TOY_FAULT=kill|hang|nan|exit and TOY_FAULT_MOD=N: the fault fires for trials where seed % N == 0,
and (for kill) only on attempt 1, so the retry path can be tested.
"""
import argparse, json, math, os, random, signal, time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True); ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--attempt", type=int, default=1); ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())
    fault, mod = os.environ.get("TOY_FAULT", ""), int(os.environ.get("TOY_FAULT_MOD", "0") or 0)
    if fault and mod and a.seed % mod == 0:
        if fault == "kill" and a.attempt == 1:
            os.kill(os.getpid(), signal.SIGKILL)
        if fault == "hang":
            time.sleep(3600)
        if fault == "exit":
            raise SystemExit(3)
    rng = random.Random(a.seed)
    noise = {"s0": 0.30, "s1": 0.08, "s2": 0.02}.get(a.stage, 0.02)
    if a.final:
        noise = 0.02
    x, b, c = spec["a"], spec["b"], spec["c"]
    val = 10 * (x - 0.3) ** 2 + (b - 7) ** 2 / 50 + {"u": 0.0, "v": 0.15, "w": 0.4}[c] + 0.5
    val += rng.gauss(0, noise)
    if fault == "nan" and mod and a.seed % mod == 0:
        val = float("nan")
    m = {"val_mse": val, "n_params": b * 10 + (20 if x > 0.5 else 0)}
    Path(a.out, "metrics.json").write_text(json.dumps(m))
    time.sleep(float(os.environ.get("TOY_SLEEP", "0")))


if __name__ == "__main__":
    main()
