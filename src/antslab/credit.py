"""Credit assignment: later outcomes flow BACK to the levers that produced them.

Every mutated child records its parent (origin_key) and the levers it changed (edit). Comparing child and
parent at the same, cheapest stage gives a per-lever improvement signal. Those signals bias which levers
future mutations touch (bandit with an exploration bonus) and are summarised for Dream / the LLM prompt.
This is the 'back-propagation' of the research loop: deep results change early decisions.
"""
from __future__ import annotations

import math

from .contract import Contract


def lever_credit(ledger, c: Contract, arm: str) -> dict:
    first = c.stages[0]
    val = {}
    for t in ledger.trials(c.gym, arm, ("done",)):
        if t["stage"] == first and c.primary in (t["metrics"] or {}):
            val[t["spec_key"]] = t["metrics"][c.primary]
    stats: dict = {}
    for t in ledger.trials(c.gym, arm, ("done",)):
        if t["stage"] != first or not t["origin_key"] or not t["edit"]:
            continue
        pv, cv = val.get(t["origin_key"]), val.get(t["spec_key"])
        if pv is None or cv is None:
            continue
        d = (pv - cv) / max(abs(pv), 1e-9)
        d = max(-1.0, min(1.0, d if c.direction == "min" else -d))
        for k in t["edit"]:
            s = stats.setdefault(k, {"n": 0, "gain": 0.0, "wins": 0})
            s["n"] += 1; s["gain"] += max(d, 0.0) / len(t["edit"]); s["wins"] += d > 0
    return stats


def lever_weights(ledger, c: Contract, arm: str, base: float = 0.02, explore: float = 0.05) -> dict:
    st = lever_credit(ledger, c, arm)
    total = sum(s["n"] for s in st.values())
    out = {}
    for n in c.space.params:
        s = st.get(n, {"n": 0, "gain": 0.0})
        out[n] = base + s["gain"] / (s["n"] + 2) + explore * math.sqrt(math.log(total + 2) / (s["n"] + 1))
    return out


def credit_report(ledger, c: Contract, arm: str) -> list[dict]:
    st = lever_credit(ledger, c, arm)
    lay = {n: p.layer or "other" for n, p in c.space.params.items()}
    rows = [{"lever": k, "layer": lay.get(k, "?"), "tries": s["n"], "win_rate": round(s["wins"] / s["n"], 3),
             "mean_gain": round(s["gain"] / s["n"], 4)} for k, s in st.items() if s["n"]]
    return sorted(rows, key=lambda r: -r["mean_gain"])
