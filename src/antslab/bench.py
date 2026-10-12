"""In-process benchmark harness for the SEARCH layer itself.

Analytic mixed discrete/continuous landscapes with a known optimum: they calibrate and unit-test the search
operators (and serve as a cheap 'Gym 0' for the system paper). No subprocesses, so thousands of evaluations per second.
"""
from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

from .contract import load_contract
from .history import History
from .ledger import Ledger

TOML = """
[task]
name="trap"
gym="trap"
description="analytic deceptive mixed landscape"
[budget]
[objective]
primary="f"
direction="min"
[cascade]
stages=["s0"]
[commands]
eval=["x"]
[space.core]
type="cat"
choices=["a","b","c","d"]
role="skeleton"
layer="architecture"
[space.norm]
type="cat"
choices=["none","last","mean"]
role="skeleton"
layer="data"
[space.mode]
type="cat"
choices=["p","q"]
layer="optim"
[space.use_aux]
type="bool"
layer="optim"
[space.aux]
type="float"
low=0.0
high=1.0
when={use_aux=[true]}
layer="optim"
[space.x1]
type="float"
low=0.0
high=1.0
layer="architecture"
[space.x2]
type="float"
low=0.0
high=1.0
layer="architecture"
[space.x3]
type="float"
low=0.0
high=1.0
layer="architecture"
[space.x4]
type="logfloat"
low=0.001
high=10.0
layer="optim"
[space.junk1]
type="float"
low=0.0
high=1.0
layer="other"
[space.junk2]
type="int"
low=1
high=50
layer="other"
"""

OFFSET = {"a": 0.30, "b": 0.45, "c": 0.20, "d": 0.00}        # d holds the global optimum...
WIDTH = {"a": 0.60, "b": 0.50, "c": 0.35, "d": 0.20}         # ...in the narrowest funnel
TARGET = {"a": (0.30, 0.70, 0.40), "b": (0.80, 0.20, 0.50), "c": (0.55, 0.45, 0.65), "d": (0.15, 0.85, 0.35)}


def trap(spec: dict, noise: float = 0.0, rng: random.Random | None = None) -> float:
    t = TARGET[spec["core"]]
    w = WIDTH[spec["core"]]
    f = OFFSET[spec["core"]]
    for xi, ti in zip((spec["x1"], spec["x2"], spec["x3"]), t):
        f += 1.0 - math.exp(-((xi - ti) / w) ** 2)                       # funnel
        f += 0.04 * (1 - math.cos(14 * (xi - ti)))                        # ripples = many shallow local optima
    f += 0.15 * abs(math.log10(spec["x4"]) - 0.0)                          # best x4 == 1
    f += {"none": 0.10, "last": 0.0, "mean": 0.05}[spec["norm"]]
    f += 0.05 if spec["mode"] == "q" else 0.0
    if spec["use_aux"]:
        f += 0.20 * (spec["aux"] - 0.5) ** 2 - 0.03                        # tiny bonus near aux=0.5
    if noise and rng:
        f += rng.gauss(0, noise)
    return f


OPTIMUM = trap({"core": "d", "norm": "last", "mode": "p", "use_aux": True, "aux": 0.5, "x1": .15, "x2": .85, "x3": .35,
                "x4": 1.0, "junk1": 0, "junk2": 1})


def make_contract(tmp: Path | None = None):
    d = Path(tmp or tempfile.mkdtemp())
    p = d / "trap.toml"
    p.write_text(TOML)
    return load_contract(p)


def drive(proposer_factory, c, n_evals: int, seed: int, batch: int = 4, noise: float = 0.01, fn=trap,
          checkpoints=(100, 300, 600, 1000), truth=None, static=None, quota=0.05, mem=None) -> dict:
    """Run propose -> evaluate -> record entirely in-process; returns best-so-far at checkpoints."""
    d = Path(tempfile.mkdtemp())
    led = Ledger(d / "l.sqlite")
    hist = History(led, c, "arm")
    prop = proposer_factory(c, seed)
    rng = random.Random(seed + 999)
    done, best, out = 0, float("inf"), {}
    while done < n_evals:
        hist.update()
        seen = led.seen_keys(c.gym, "arm")
        props = prop.propose(led, "arm", batch, seen, hist=hist)
        if not props:
            props = [type("P", (), {"spec": c.space.sample(rng), "source": "random", "origin_key": None, "edit": None})()]
        if static is not None:                      # same free pre-filter the Campaign applies: certain cap violations cost nothing
            cap = max(c.caps)
            props = [p for p in props if static(p.spec) <= cap or rng.random() < quota]
        if mem is not None:                          # resource cap (estimated peak MB): infeasible regardless of any quota
            props = [p for p in props if mem[0](p.spec) <= mem[1]]
        for p in props:
            tid = led.enqueue(c.gym, "arm", seed * 10_000 + done, p.spec, p.source, stage="s0",
                              spec_key=c.space.key(p.spec), origin_key=p.origin_key, edit=p.edit)
            t = led.claim(60, c.gym, "arm")
            m = fn(p.spec, noise, rng)
            m = m if isinstance(m, dict) else {c.primary: m}
            led.finish(t["id"], t["attempt"], "done", m, None, 0.0, None)
            tv = truth(p.spec) if truth else (trap(p.spec) if fn is trap else m[c.primary])
            best = min(best, tv)
            done += 1
            if done in checkpoints:
                out[done] = best
    return {"best": out, "hist": hist, "prop": prop}
