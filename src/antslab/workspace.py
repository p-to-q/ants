"""A snapshot of the ledger as a directory of plain files, for an agent (or a person with grep) to read. Standard library only.

Meta-Harness's proposer works on a filesystem of every prior candidate's code, scores and traces (docs/research/meta-harness.md).
ants keeps the same experience in a hash-chained ledger; this writes it out in a form that `grep`, `cat` and `jq` understand:

    SKILL.md              what is here, what may be done with it, what may not
    trials.jsonl          one line per trial: spec, metrics per stage, state, error, lineage
    frontier.json         best specs per parameter cap, ranked by the deepest stage reached
    failures.jsonl        the commonest error messages
    trials/<id>/          for the best and the most recent trials: spec.json, metrics.json, lineage.json

It is a snapshot (read-only by convention: nothing reads it back), it contains only what the search itself produced (sealed and confirm
data never enter the ledger), and every string that came from outside our code is cleaned before it is written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import ui
from .history import History
from .textsafe import clean_text

SKILL = """# ants workspace

A read-only snapshot of one search. Everything in it was written by the search; nothing here can change how a model is scored.

## Files
- `trials.jsonl`: one JSON object per trial (newest last): id, arm, stage, state, source (the operator that proposed it), spec_key,
  origin_key (the spec it was derived from), edit (levers changed from that parent), params (the spec), metrics (that stage), error, cpu_s, epoch.
- `frontier.json`: best specs per parameter cap. Ranked by the deepest stage reached, then by value there; values of different stages are
  different protocols and are not comparable with each other.
- `failures.jsonl`: error messages with counts.
- `trials/<id>/`: spec.json, metrics.json and lineage.json for the best and the most recent trials.

## How to use it
- Read failures and the differences between the best specs and the rest before proposing anything.
- Prefer a change of mechanism (a different core, a lever combination, a structure) to a numeric tweak of an existing spec: parameter variants usually tie.
- Write down a hypothesis for each proposal: what you expect to change, and why.

## What you may not do
- Do not look for, ask for or use data other than what is here. Held-out datasets are not part of this search.
- Do not try to change the evaluator, the contract, the ledger or this directory. Proposals are specs in the allowed key ranges, nothing else.
- Do not copy a spec you have seen; the search discards duplicates.
"""


def _row(t: dict) -> dict:
    return {"id": t["id"], "arm": t["arm"], "stage": t["stage"], "state": clean_text(t["state"], 20), "source": clean_text(t["source"], 60),
            "spec_key": t["spec_key"], "origin_key": t.get("origin_key"), "edit": t.get("edit"), "params": t["params"], "metrics": t["metrics"],
            "error": clean_text(t["error"], 300) if t.get("error") else None, "cpu_s": t.get("cpu_s"), "epoch": t.get("epoch")}


def _write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def export_workspace(led, c, arm: str, out, limit: int = 3000, per_trial: int = 60) -> dict:
    out = Path(out)
    (out / "trials").mkdir(parents=True, exist_ok=True)
    rows = [_row(t) for t in led.trials(c.gym, arm)][-limit:]
    _write(out / "trials.jsonl", "".join(json.dumps(r, default=str) + "\n" for r in rows))
    h = History(led, c, arm)
    lb = ui.leaderboard(h, c, top=10)
    _write(out / "frontier.json", json.dumps(lb, indent=1, default=str))
    fails = led.conn.execute("SELECT substr(error,1,160) AS e, COUNT(*) AS n FROM trials WHERE gym=? AND arm=? AND error IS NOT NULL AND error<>'' GROUP BY 1 ORDER BY n DESC LIMIT 50", (c.gym, arm)).fetchall()
    _write(out / "failures.jsonl", "".join(json.dumps({"error": clean_text(r["e"], 160), "n": r["n"]}) + "\n" for r in fails))
    pick = [r["trial"] for rs in lb.values() for r in rs][: per_trial // 2] + [r["id"] for r in rows[-(per_trial // 2):]]
    done = set()
    for tid in pick:
        if tid in done:
            continue
        done.add(tid)
        d = ui.show(led, c, str(tid))
        if "not_found" in d:
            continue
        td = out / "trials" / str(tid)
        td.mkdir(exist_ok=True)
        _write(td / "spec.json", json.dumps(d["spec"], indent=1))
        _write(td / "metrics.json", json.dumps(d["stages"], indent=1, default=str))
        _write(td / "lineage.json", json.dumps({"edit_from_parent": d["edit_from_parent"], "lineage": d["lineage"]}, indent=1, default=str))
    _write(out / "SKILL.md", SKILL)
    return {"trials": len(rows), "detailed": len(done), "out": str(out)}


def diff_trials(led, c, a_ref: str, b_ref: str) -> dict:
    a, b = ui.show(led, c, a_ref), ui.show(led, c, b_ref)
    if "not_found" in a or "not_found" in b:
        return {"not_found": a.get("not_found") or b.get("not_found")}
    keys = sorted(set(a["spec"]) | set(b["spec"]))
    return {"a": a["trial"], "b": b["trial"],
            "levers": {k: [a["spec"].get(k), b["spec"].get(k)] for k in keys if a["spec"].get(k) != b["spec"].get(k)},
            "stages": {s: [a["stages"].get(s, {}).get(c.primary), b["stages"].get(s, {}).get(c.primary)] for s in sorted(set(a["stages"]) | set(b["stages"]))}}
