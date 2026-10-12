"""Automated research report: deterministic tables + an LLM-written narrative that is *linted*.

The narrative may only be written from `facts.json`. Every number the LLM writes must be traceable to a number
in the facts (within display rounding); anything else is flagged and the paragraph is marked UNVERIFIED.
This keeps the cheap, fluent part (prose) from ever inventing the expensive part (evidence).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .contract import Contract
from .proposers import rank_specs

NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _curve(ledger, c: Contract, arm: str, points: int = 12) -> list[dict]:
    """Best-so-far (at the deepest stage that has data) versus cumulative CPU seconds."""
    rows = [t for t in ledger.trials(c.gym, arm) if t["state"] in ("done", "failed", "timeout", "crashed")]
    first, cum, best, out = c.stages[0], 0.0, None, []
    for t in rows:
        cum += t["cpu_s"] or 0.0
        v = (t["metrics"] or {}).get(c.primary)
        if t["stage"] == first and v is not None and (best is None or c.better(v, best)):
            best = v
        out.append((cum, best))
    if not out:
        return []
    step = max(1, len(out) // points)
    return [{"cpu_s": round(a, 1), "best_s0": b} for a, b in out[::step]] + [{"cpu_s": round(out[-1][0], 1), "best_s0": out[-1][1]}]


def facts(ledger, c: Contract, arms: list[str], baselines: dict | None = None) -> dict:
    f = {"task": c.name, "primary": c.primary, "direction": c.direction, "stages": c.stages,
         "chain_ok": ledger.verify_chain()[0], "chain_head": ledger.head()[:16],
         "sealed_evaluations_used": ledger.unseal_count(c.gym), "arms": {}, "baselines": baselines or {}}
    for arm in arms:
        trials = ledger.trials(c.gym, arm)
        by_state, by_stage, by_source = {}, {}, {}
        for t in trials:
            by_state[t["state"]] = by_state.get(t["state"], 0) + 1
            if t["state"] == "done":
                by_stage[t["stage"]] = by_stage.get(t["stage"], 0) + 1
            by_source[t["source"].split(":")[0]] = by_source.get(t["source"].split(":")[0], 0) + 1
        ranked = rank_specs(ledger, c, arm)
        caps = sorted(c.caps) or [None]
        best = {}
        for cb in caps:
            for r in ranked:
                cap_val = r["metrics"].get(c.cap_metric, 0) if c.cap_metric else 0
                if cb is None or cap_val <= cb:
                    best[str(cb)] = {"value": round(r["value"], 5), "stage": c.stages[r["stage_idx"]],
                                     "n_params": cap_val, "trial_id": r["trial_id"]}
                    break
        f["arms"][arm] = {"trials": len(trials), "by_state": by_state, "done_by_stage": by_stage,
                          "proposals_by_source": by_source, "cpu_s": round(ledger.cpu_total(c.gym, arm), 1),
                          "best_by_cap": best, "anytime": _curve(ledger, c, arm)}
    calls = ledger.conn.execute("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM llm_calls").fetchone()
    f["llm_calls"] = {"total": calls[0], "ok": calls[1]}
    return f


def tables_md(f: dict) -> str:
    lines = [f"# {f['task']}: results", "",
             f"Ledger hash-chain intact: **{f['chain_ok']}** (head `{f['chain_head']}`). "
             f"Sealed evaluations used: {f['sealed_evaluations_used']}.", ""]
    caps = sorted({k for a in f["arms"].values() for k in a["best_by_cap"]}, key=lambda x: float(x) if x != "None" else 1e18)
    lines += ["| arm | trials | CPU s | " + " | ".join(f"best @≤{k} params" for k in caps) + " |",
              "|---|---|---|" + "---|" * len(caps)]
    for arm, a in f["arms"].items():
        cells = [f"{a['best_by_cap'][k]['value']} ({a['best_by_cap'][k]['stage']})" if k in a["best_by_cap"] else "–" for k in caps]
        lines.append(f"| {arm} | {a['trials']} | {a['cpu_s']} | " + " | ".join(cells) + " |")
    if f["baselines"]:
        lines += ["", "Reference baselines (same protocol, same stage as noted):", "",
                  "| baseline | params | val_mse |", "|---|---|---|"]
        lines += [f"| {k} | {v['n_params']} | {round(v['val_mse'], 5)} |" for k, v in f["baselines"].items()]
    return "\n".join(lines) + "\n"


# ---- number linter ---------------------------------------------------------
def _flat_numbers(obj, acc):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.append(float(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            for tok in NUM.findall(str(k)):
                acc.append(float(tok))
            _flat_numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flat_numbers(v, acc)
    elif isinstance(obj, str):
        for tok in NUM.findall(obj):
            acc.append(float(tok))


def lint_numbers(text: str, facts_obj: dict, rel_tol: float = 0.01) -> list[str]:
    """Return numbers in `text` that cannot be matched to `facts_obj`. Code spans and list markers are ignored."""
    allowed: list[float] = []
    _flat_numbers(facts_obj, allowed)
    clean = re.sub(r"`[^`]*`", " ", text)
    clean = re.sub(r"^\s*\d+[.)]\s", " ", clean, flags=re.M)
    clean = re.sub(r"^#+\s.*$", " ", clean, flags=re.M)
    bad = []
    for m in NUM.finditer(clean):
        tok = m.group(0)
        x = float(tok)
        decimals = len(tok.split(".")[1]) if "." in tok and "e" not in tok.lower() else 0
        half_ulp = 0.5 * 10 ** (-decimals)
        ok = any(abs(x - a) <= max(half_ulp, rel_tol * abs(a)) if decimals else x == a or abs(x - a) <= rel_tol * abs(a)
                 for a in allowed)
        if not ok and tok not in bad:
            bad.append(tok)
    return bad


def draft_narrative(client, f: dict, max_repairs: int = 1) -> tuple[str, list[str]]:
    sys = ("You write a short, sober research-progress note for engineers. Use ONLY the numbers in FACTS. "
           "Do not state any number that is not in FACTS. Say plainly when a result is not significant, when "
           "budgets differ, or when evidence is missing. No marketing language. Reply with JSON {\"text\": \"markdown\"}.")
    user = "FACTS:\n" + json.dumps(f, indent=1, default=str)[:12000]
    text, bad = "", []
    for attempt in range(max_repairs + 1):
        msgs = [{"role": "system", "content": sys}, {"role": "user", "content": user + (
            f"\n\nYour previous draft used numbers not in FACTS: {bad}. Rewrite without them." if bad else "")}]
        obj, _ = client.chat_json(msgs, temperature=0.3)
        text = (obj or {}).get("text", "")
        bad = lint_numbers(text, f)
        if text and not bad:
            return text, []
    return ("> **UNVERIFIED** — numbers not traceable to the ledger: " + ", ".join(bad) + "\n\n" + text) if text else "", bad


def write_report(ledger, c: Contract, arms: list[str], out_dir, client=None, baselines=None) -> Path:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    f = facts(ledger, c, arms, baselines)
    (out / "facts.json").write_text(json.dumps(f, indent=1, default=str))
    md = tables_md(f)
    if client is not None:
        text, bad = draft_narrative(client, f)
        md += "\n## Narrative (LLM-drafted, number-linted)\n\n" + (text or "_no narrative produced_") + "\n"
        ledger.event("report_narrative", {"unverified_numbers": bad, "ok": not bad})
    (out / "report.md").write_text(md)
    return out / "report.md"
