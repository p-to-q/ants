"""Landscape diagnostics: make the search terrain legible (for the board, the report and the paper)."""
from __future__ import annotations

from .lineage import stepping_stats


def niche_table(h, top: int = 30) -> list[dict]:
    """One row per skeleton niche: level, velocity, acceleration and a label.
    leader   best level in the run          riser   not the leader, but improving fast / accelerating
    stalled  mature and not improving       young   too few evaluations to say"""
    rows = []
    for nz, d in h.niches.items():
        t = h.trajectory(nz)
        rows.append({"niche": nz, "n": t["n"], "best": h.sign * (t["level"] if t["level"] is not None else float("nan")),
                     "velocity": t["velocity"], "acceleration": t["acceleration"], "potential": h.potential(nz)})
    rows.sort(key=lambda r: h.sign * r["best"])
    for i, r in enumerate(rows):
        if r["n"] < 16:
            r["label"] = "young"
        elif i == 0:
            r["label"] = "leader"
        elif r["velocity"] > 1e-4 or r["acceleration"] > 1e-4:
            r["label"] = "riser"
        else:
            r["label"] = "stalled"
    return rows[:top]


def ruggedness(h) -> float | None:
    """Parent/child correlation of the objective over lineage pairs: ~1 smooth landscape, ~0 rugged."""
    xs, ys = [], []
    for r in h.order:
        p = h.first.get(r["origin_key"]) if r["origin_key"] else None
        if p is not None and r.get("feasible", True) and p.get("feasible", True):
            xs.append(p["value"]); ys.append(r["value"])
    if len(xs) < 10:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5; sy = sum((y - my) ** 2 for y in ys) ** .5
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 3) if sx > 0 and sy > 0 else None


def plateau_fraction(h, tol: float = 1e-9) -> float | None:
    """Fraction of children indistinguishable from their parent: dormant or inert levers."""
    n = same = 0
    for r in h.order:
        p = h.first.get(r["origin_key"]) if r["origin_key"] else None
        if p is not None:
            n += 1; same += abs(r["value"] - p["value"]) <= tol * max(1.0, abs(p["value"]))
    return round(same / n, 3) if n else None


def diagnostics(h) -> dict:
    return {"evaluations": len(h.order), "feasible": sum(1 for r in h.order if r.get("feasible", True)),
            "ruggedness": ruggedness(h), "plateau_fraction": plateau_fraction(h),
            "stagnant_for": h.stagnant_for, "operators": {k: dict(v) for k, v in h.ops.items()},
            "delayed_credit": {k: round(v, 3) for k, v in h.delayed_credit().items()},
            "retired_levers": sorted(f"{k[0]}={k[1]}" for k in h.retired()), "niches": niche_table(h),
            "stepping_stones": stepping_stats(h)}
