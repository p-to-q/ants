"""Lineage analysis: tests the stepping-stone hypothesis ('the next best model need not descend from the current best').

For every evaluated spec we know its rank percentile AT THE TIME it was evaluated (among everything evaluated earlier).
Walking a champion's ancestry then shows whether its ancestors were leaders or unremarkable 'stepping stones'.
"""
from __future__ import annotations

import bisect


def percentiles_at_time(h) -> dict:
    """rec id -> fraction of earlier first-stage evaluations that were strictly better (0 = new leader)."""
    srt, out = [], {}
    for r in h.order:
        sv = h.sign * r["value"]
        out[r["id"]] = (bisect.bisect_left(srt, sv) / len(srt)) if srt else 0.0
        bisect.insort(srt, sv)
    return out


def ancestry(h, key: str, max_depth: int = 60) -> list[dict]:
    chain, seen = [], set()
    rec = h.first.get(key)
    while rec and rec["key"] not in seen and len(chain) < max_depth:
        chain.append(rec); seen.add(rec["key"])
        rec = h.first.get(rec["origin_key"]) if rec["origin_key"] else None
    return chain


def stepping_stats(h, top_n: int = 5, leader_cut: float = 0.25) -> dict:
    pct = percentiles_at_time(h)
    champs = sorted((r for r in h.first.values() if r.get("feasible", True)), key=lambda r: h.sign * r["value"])[:top_n]
    non_leader, total, depth, drops = 0, 0, [], []
    for ch in champs:
        chain = ancestry(h, ch["key"])[1:]                       # ancestors only
        depth.append(len(chain))
        for a in chain:
            total += 1
            non_leader += pct.get(a["id"], 0.0) > leader_cut
        if chain:
            drops.append(max(pct.get(a["id"], 0.0) for a in chain))
    # lead change: was the skeleton of the final champion the leading skeleton halfway through the run?
    sp, n = h.c.space, len(h.order)
    half = h.order[: max(1, n // 2)]
    lead_half = min((r for r in half if r.get("feasible", True)), key=lambda r: h.sign * r["value"], default=half[0])
    champ = champs[0]
    return {"n_evals": n, "champions": len(champs), "mean_depth": round(sum(depth) / max(1, len(depth)), 2),
            "ancestors": total,
            "non_leader_ancestor_frac": round(non_leader / total, 3) if total else None,
            "worst_ancestor_pct": round(max(drops), 3) if drops else None,
            "lead_changed": sp.niche(champ["spec"]) != sp.niche(lead_half["spec"])}
