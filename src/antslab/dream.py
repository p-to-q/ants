"""Dream: offline consolidation of the ledger into short, VERIFIED lessons (Auto-Dream-style).

1 review      pull the arm's completed first-stage trials
2 extract     deterministic per-parameter-value effects + failure clusters
3 reconcile   an LLM may *word* the lessons, but every claim is re-checked against the statistics;
              unsupported claims are dropped (an LLM cannot plant beliefs in the memory)
4 prune       fixed line budget, strongest evidence first
"""
from __future__ import annotations

import re

import statistics as st
from collections import Counter

from .contract import Contract


def _groups(ledger, c: Contract, arm: str):
    first = c.stages[0]
    rows = [(t["params"], t["metrics"][c.primary]) for t in ledger.trials(c.gym, arm, states=("done",))
            if t["stage"] == first and c.primary in (t["metrics"] or {})]
    return rows


def effects(ledger, c: Contract, arm: str, min_n: int = 3) -> list[dict]:
    rows = _groups(ledger, c, arm)
    if len(rows) < 2 * min_n:
        return []
    overall = st.mean(v for _, v in rows)
    out = []
    for n, p in c.space.params.items():
        buckets: dict = {}
        for spec, v in rows:
            if not c.space.is_active(n, spec):
                continue
            if p.kind in ("cat", "bool"):
                key = spec[n]
            else:                                              # numeric: terciles of the observed range
                xs = sorted(s[n] for s, _ in rows if c.space.is_active(n, s))
                lo, hi = xs[len(xs) // 3], xs[2 * len(xs) // 3]
                key = "low" if spec[n] <= lo else ("high" if spec[n] >= hi else "mid")
            buckets.setdefault(key, []).append(v)
        for key, vs in buckets.items():
            if len(vs) >= min_n:
                m = st.mean(vs)
                gain = (overall - m) if c.direction == "min" else (m - overall)
                out.append({"param": n, "value": key, "mean": m, "n": len(vs), "gain": gain})
    return sorted(out, key=lambda e: -e["gain"])


def failure_clusters(ledger, c: Contract, arm: str) -> list[tuple[str, int]]:
    cnt = Counter()
    for t in ledger.trials(c.gym, arm, states=("failed", "timeout", "crashed")):
        cnt[(t["error"] or t["state"]).split(":")[0][:60]] += 1
    return cnt.most_common(5)


def dream(ledger, c: Contract, arm: str, client=None, max_lines: int = 12) -> list[str]:
    eff = effects(ledger, c, arm)
    lines = []
    for e in eff[: max_lines // 2]:
        if e["gain"] > 0:
            lines.append(f"{e['param']}={e['value']} is better than average (mean {c.primary}={e['mean']:.4g}, n={e['n']})")
    for e in reversed(eff[-(max_lines // 2):]):
        if e["gain"] < 0:
            lines.append(f"{e['param']}={e['value']} is worse than average (mean {c.primary}={e['mean']:.4g}, n={e['n']})")
    for msg, n in failure_clusters(ledger, c, arm):
        lines.append(f"failure x{n}: {msg}")
    dropped = 0
    if client is not None and eff:
        stats = "\n".join(f"{e['param']}={e['value']}: mean={e['mean']:.4g} n={e['n']} gain={e['gain']:.3g}"
                          for e in eff[:30])
        obj, _ = client.chat_json([
            {"role": "system", "content": "You summarise experiment statistics. Reply with ONLY JSON."},
            {"role": "user", "content": f"Statistics (positive gain = better):\n{stats}\n\nReturn "
             '{"lessons":[{"param":"...","value":"...","claim":"better|worse","note":"short insight"}]} '
             "with at most 6 lessons. Only cite parameter/value pairs from the statistics."}])
        lookup = {(e["param"], str(e["value"])): e for e in eff}
        raw = obj.get("lessons") if isinstance(obj, dict) else None
        for l in (raw if isinstance(raw, list) else [])[:6]:
            if not isinstance(l, dict):                       # structurally wrong model output is dropped, never fatal
                dropped += 1
                continue
            e = lookup.get((str(l.get("param")), str(l.get("value"))))
            ok = e and ((l.get("claim") == "better" and e["gain"] > 0) or (l.get("claim") == "worse" and e["gain"] < 0))
            if ok:
                note = re.sub(r"[^A-Za-z0-9 .,=%()/+-]", "", str(l.get("note", "")))[:100]      # free text is never stored raw
                lines.append(f"{l['param']}={l['value']} {l['claim']}: {note} (n={e['n']})")
            else:
                dropped += 1
    lines = list(dict.fromkeys(lines))[:max_lines]
    ledger.event("dream", {"arm": arm, "lines": lines, "unsupported_dropped": dropped,
                           "n_trials": len(_groups(ledger, c, arm))})
    return lines


def latest_memory(ledger, arm: str) -> str:
    for e in reversed(ledger.events("dream")):
        if e["payload"].get("arm") == arm:
            return "\n".join("- " + l for l in e["payload"]["lines"])
    return ""
