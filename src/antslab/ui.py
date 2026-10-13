"""Human- and agent-facing interface: snapshot / leaderboard / show / log / hint / pause / doctor, all returning plain dicts
(printed as text for people, or as JSON with --json for other agents). Read-only except `hint` (an event) and the PAUSE flag file."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .board import _diff
from .history import History
from .janitor import disk_level, free_gb
from .landscape import niche_table
from .textsafe import clean_text as _c      # everything that did not originate in our code is passed through this before it is shown

PAUSE_FILE = "PAUSE"


def _q1(led, sql, *a):
    r = led.conn.execute(sql, a).fetchone()
    return r[0] if r else None


def snapshot(led, c, arm: str, workdir, hist: History | None = None) -> dict:
    h = hist or History(led, c, arm)
    h.update()
    now = time.time()
    by = {}
    for stage, state, n in led.conn.execute("SELECT stage, state, COUNT(*) FROM trials WHERE gym=? AND arm=? GROUP BY stage, state", (c.gym, arm)):
        by.setdefault(stage, {})[state] = n
    fin = lambda dt: _q1(led, "SELECT COUNT(*) FROM trials WHERE gym=? AND arm=? AND finished_at>?", c.gym, arm, now - dt)
    last = _q1(led, "SELECT MAX(finished_at) FROM trials WHERE gym=? AND arm=?", c.gym, arm)
    ok, bad = led.verify_tail()
    fg = free_gb(workdir)
    lb = leaderboard(h, c, top=3)
    calls = led.conn.execute("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM llm_calls WHERE ts>?", (now - 3600,)).fetchone()
    return {"arm": arm, "task": _c(c.name, 100), "epoch": _q1(led, "SELECT epoch FROM trials WHERE gym=? AND arm=? ORDER BY id DESC LIMIT 1", c.gym, arm),
            "stages": by, "finished_5min": fin(300), "finished_1h": fin(3600), "last_finish_age_s": (now - last) if last else None,
            "paused": (Path(workdir) / PAUSE_FILE).exists(), "free_gb": round(fg, 1), "disk_level": disk_level(fg, c.thresholds),
            "chain_tail_ok": ok, "head": led.head()[:12], "best_by_cap": lb,
            "operators": {_c(k, 40): {"n": v["n"], "rate": round(v["succ"] / max(1, v["n"]), 3)} for k, v in sorted(h.ops.items(), key=lambda kv: -kv[1]["succ"] / max(1, kv[1]["n"]))},
            "paths": [{"path": _c("/".join(map(str, n["niche"])), 80), "n": n["n"], "best": round(n["best"], 4), "label": _c(n["label"], 40)} for n in niche_table(h)[:6]],
            "llm_calls_1h": {"calls": calls[0], "ok": calls[1]}, "user_hints": [_c(e["payload"].get("text", ""), 500) for e in led.recent_events("user_hint", 3)]}


def leaderboard(h: History, c, top: int = 5) -> dict:
    """Best specs per cap bin. Ranked by the deepest stage each spec reached, then by value at that stage: a cheap first-stage
    number must never outrank a deeper measurement (stages are different protocols and not comparable)."""
    h.update()                                   # always current: callers must not have to remember this
    out, bins = {}, {}
    for r in sorted((r for r in h.best.values() if r.get("feasible", True)), key=lambda r: (-r["stage_idx"], h.sign * r["value"])):
        bins.setdefault(h.cap_bin(r["metrics"]), []).append(r)
    for b, rs in sorted(bins.items(), key=lambda kv: kv[0]):
        if b == float("inf"):
            continue
        out[str(b)] = [{"rank": i, "value": round(r["value"], 5), "stage": c.stages[r["stage_idx"]], "n_params": r["metrics"].get(c.cap_metric),
                        "trial": r["id"], "key": r["key"], "operator": _c(((h.first.get(r["key"]) or r)["source"]).split(":")[0], 40), "spec": _diff(c, r["spec"])}
                       for i, r in enumerate(rs[:top], 1)]
    return out


def show(led, c, ref: str) -> dict:
    row = (led.conn.execute("SELECT * FROM trials WHERE id=?", (int(ref),)).fetchone() if ref.isdigit()
           else led.conn.execute("SELECT * FROM trials WHERE spec_key LIKE ? ORDER BY id LIMIT 1", (ref + "%",)).fetchone())
    if row is None:
        return {"not_found": f"no trial matches {ref!r}"}
    t = led._row(row)
    chain, key, seen = [], t["origin_key"], set()                 # (strings below are cleaned: they may come from outside)
    while key and key not in seen and len(chain) < 40:
        seen.add(key)
        r = led.conn.execute("SELECT * FROM trials WHERE gym=? AND spec_key=? AND stage=? ORDER BY id LIMIT 1", (c.gym, key, c.stages[0])).fetchone()
        if r is None:
            break
        p = led._row(r)
        chain.append({"trial": p["id"], "value": (p["metrics"] or {}).get(c.primary), "source": _c(p["source"], 60),
                      "edit": [_c(x, 40) for x in p["edit"]] if isinstance(p["edit"], list) else p["edit"]})
        key = p["origin_key"]
    same = [led._row(r) for r in led.conn.execute("SELECT * FROM trials WHERE gym=? AND spec_key=? ORDER BY id", (c.gym, t["spec_key"]))]
    return {"trial": t["id"], "spec_key": t["spec_key"], "arm": t["arm"], "epoch": t["epoch"], "state": _c(t["state"], 20), "source": _c(t["source"], 60),
            "spec": t["params"], "non_default": _diff(c, t["params"]), "stages": {x["stage"]: x["metrics"] for x in same if x["metrics"]},
            "error": _c(t["error"], 500) if t["error"] else t["error"],
            "edit_from_parent": [_c(x, 40) for x in t["edit"]] if isinstance(t["edit"], list) else t["edit"], "lineage": chain}


def log(led, n: int = 20, kind: str | None = None) -> list[dict]:
    q = "SELECT seq, ts, kind, payload FROM events" + (" WHERE kind=?" if kind else "") + " ORDER BY seq DESC LIMIT ?"
    rows = led.conn.execute(q, ((kind, n) if kind else (n,))).fetchall()[::-1]
    out = []
    for r in rows:
        p = json.loads(r["payload"])
        p = p if isinstance(p, dict) else {}
        brief = {k: (_c(p[k], 200) if isinstance(p[k], str) else p[k] if isinstance(p[k], (int, float)) or p[k] is None else _c(p[k], 200))
                 for k in ("id", "stage", "state", "source", "path", "text", "freeze_hash", "name", "to") if k in p}
        if "metrics" in p and isinstance(p["metrics"], dict):
            brief["metrics"] = {k: round(v, 4) for k, v in list(p["metrics"].items())[:3] if isinstance(v, (int, float))}
        out.append({"seq": r["seq"], "time": time.strftime("%m-%d %H:%M:%S", time.localtime(r["ts"])), "kind": _c(r["kind"], 40), **brief})
    return out


def hint(led, text: str) -> int:
    """Human feedback for the proposers (LLM prompts include the latest hints). It is data, never an instruction to the gates."""
    text = _c(re.sub(r"\s+", " ", str(text)).strip(), 500)          # one plain line: a hint cannot forge a prompt section or a log line
    led.event("user_hint", {"text": text})
    return len(led.recent_events("user_hint", 1000))


def doctor(led, c, arm: str, workdir) -> list[tuple[str, str]]:
    """(level, message) with level in OK / WARN / FAIL. Exit status of `ants doctor` is non-zero on FAIL."""
    out, now = [], time.time()
    ok, bad = led.verify_chain() if led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] < 300_000 else led.verify_tail(5000)
    out.append(("OK", "hash chain intact") if ok else ("FAIL", f"hash chain broken at event {bad}"))
    fg = free_gb(workdir); lvl = disk_level(fg, c.thresholds)
    out.append(({"ok": "OK", "warn": "WARN"}.get(lvl, "FAIL"), f"disk free {fg:.1f} GB (level {lvl})"))
    last = _q1(led, "SELECT MAX(finished_at) FROM trials WHERE gym=? AND arm=?", c.gym, arm)
    queued = _q1(led, "SELECT COUNT(*) FROM trials WHERE gym=? AND arm=? AND state IN ('queued','running')", c.gym, arm)
    paused = (Path(workdir) / PAUSE_FILE).exists()
    stuck_after = max(1800, 2 * max([c.trial_timeout_s, *c.stage_timeouts.values()]))      # a legitimate s2 trial may run for its whole timeout
    if paused:
        out.append(("WARN", "paused by the PAUSE flag file"))
    elif last is None:
        out.append(("WARN", "no finished trial yet"))
    elif now - last > stuck_after and queued:
        out.append(("FAIL", f"no trial finished for {(now - last) / 60:.0f} min while work is queued (stuck?)"))
    else:
        out.append(("OK", f"last trial finished {(now - last) / 60:.1f} min ago"))
    recent = led.recent_final(c.gym, arm, 200)
    if recent:
        bad_rate = sum(s != "done" for s, _ in recent) / len(recent)
        out.append(("OK" if bad_rate < .1 else ("WARN" if bad_rate < .4 else "FAIL"), f"failure rate over the last {len(recent)} trials: {bad_rate:.0%}"))
    stale = _q1(led, "SELECT COUNT(*) FROM trials WHERE state='running' AND lease_until IS NOT NULL AND lease_until<?", now)
    out.append(("OK", "no stale runners") if not stale else ("WARN", f"{stale} trial(s) running past their lease (will be reclaimed on restart)"))
    pend = _q1(led, "SELECT COUNT(*) FROM artifacts WHERE archived_uri IS NULL AND deleted_at IS NULL")
    out.append(("OK" if pend < 5000 else "WARN", f"{pend} artifact(s) not yet archived"))
    calls = led.conn.execute("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM llm_calls WHERE ts>?", (now - 86400,)).fetchone()
    if calls[0]:
        out.append(("OK" if calls[1] / calls[0] > .6 else "WARN", f"LLM calls in 24 h: {calls[0]}, parseable {calls[1] / calls[0]:.0%}"))
    starts = [json.loads(r[0]) for r in led.conn.execute("SELECT payload FROM events WHERE kind='daemon_started' ORDER BY seq")]
    hashes = []
    for s in starts:
        h = s.get("arm_def_hash") if isinstance(s, dict) else None
        if h and (not hashes or hashes[-1] != h):
            hashes.append(h)
    if len(hashes) > 1:
        out.append(("WARN", f"the arm's definition changed {len(hashes) - 1} time(s) inside this ledger: results before and after are not one experiment; "
                            "start a fresh workdir to compare (docs/upgrading.md)"))
    ep = _q1(led, "SELECT epoch FROM trials WHERE gym=? AND arm=? ORDER BY id DESC LIMIT 1", c.gym, arm)
    n_ep = _q1(led, "SELECT COUNT(*) FROM epochs")
    out.append(("OK" if n_ep <= 1 else "WARN", f"{n_ep} epoch(s) recorded; current {ep or '-'}" + ("" if n_ep <= 1 else " (evaluator, contract or data changed during this ledger's life)")))
    return out


def epochs(led, c, arm: str | None = None) -> list[dict]:
    """every measuring epoch the ledger has seen: how many trials, when, and which versions of ants ran in it"""
    rows = led.conn.execute("SELECT epoch, COUNT(*), MIN(created_at), MAX(finished_at), SUM(state='done') FROM trials WHERE gym=? GROUP BY epoch ORDER BY MIN(id)", (c.gym,)).fetchall()
    ver: dict = {}
    for r in led.conn.execute("SELECT payload FROM events WHERE kind='daemon_started' ORDER BY seq"):
        p = json.loads(r[0])
        if isinstance(p, dict):
            ver.setdefault(p.get("epoch"), []).append(p.get("version"))
    out = []
    for ep, n, t0, t1, done in rows:
        vs = list(dict.fromkeys(ver.get(ep, [])))
        out.append({"epoch": ep or "(none)", "trials": n, "done": done or 0, "first": t0, "last": t1, "versions": vs})
    return out


def render_top(s: dict) -> str:
    B, R = "\033[1m", "\033[0m"
    L = [f"{B}ants{R} {_c(s['task'], 100)} · arm {_c(s['arm'], 60)} · epoch {s['epoch']} · chain {'ok' if s['chain_tail_ok'] else 'BROKEN'} {s['head']}"
         + ("  [PAUSED]" if s["paused"] else ""),
         f"disk {s['free_gb']} GB free ({s['disk_level']}) · finished: {s['finished_5min']} in 5 min, {s['finished_1h']} in 1 h"
         + (f" · last {s['last_finish_age_s']:.0f}s ago" if s["last_finish_age_s"] is not None else ""), ""]
    L.append(f"{B}stages{R}  " + "   ".join(f"{k}: " + ", ".join(f"{n} {st}" for st, n in v.items()) for k, v in sorted(s["stages"].items())))
    L.append(f"\n{B}best per parameter cap{R}")
    for cap, rows in s["best_by_cap"].items():
        r = rows[0]
        L.append(f"  ≤{cap:>5}  {r['value']:.4f} {r['stage']}  ({r['n_params']} params, trial {r['trial']}, via {_c(r['operator'], 40)})  {_c(r['spec'], 60)}")
    L.append(f"\n{B}search mechanisms{R} (discovery rate)  " + "  ".join(f"{_c(k, 40)} {v['rate']:.2f}({v['n']})" for k, v in list(s["operators"].items())[:8]))
    L.append(f"{B}research paths{R}  " + "  ".join(f"{_c(p['path'], 80)}[{_c(p['label'], 40)}] {p['best']}" for p in s["paths"][:4]))
    L.append(f"{B}LLM{R} last hour: {s['llm_calls_1h']['ok']}/{s['llm_calls_1h']['calls']} parseable" + (f" · hints: {_c(s['user_hints'][-1], 60)!r}" if s["user_hints"] else ""))
    return "\n".join(L)


def _num(x, fmt: str, width: int = 0) -> str:
    """Format a metric that came from a gym's metrics.json (only the headline metric is validated as a number)."""
    ok = isinstance(x, (int, float)) and not isinstance(x, bool)
    return format(x, fmt).rjust(width) if ok else _c(x, width or 8).rjust(width) if x is not None else "-".rjust(width)


def render_show(d: dict) -> str:
    if "not_found" in d:
        return _c(d["not_found"], 200)
    L = [f"trial {d['trial']}  ({_c(d['state'], 20)}, arm {_c(d['arm'], 60)}, epoch {_c(d['epoch'], 40)}, proposed by {_c(d['source'], 60)})",
         f"spec key {_c(d['spec_key'], 40)}", f"non-default levers: {_c(d['non_default'], 140)}"]
    if d["stages"]:
        L.append("\nstage   " + "rel_mse   rel_mae   params   fit_s")
        for st, m in sorted(d["stages"].items()):
            L.append(f"  {_c(st, 5):5s} {_num(m.get('rel_mse', m.get('val_mse')), '.4f', 8)}  {_num(m.get('rel_mae'), '.4f', 8)}  "
                     f"{_num(m.get('n_params'), 'g', 6)}  {_num(m.get('fit_s'), '.1f', 6)}")
        deep = {k: v for st, m in d["stages"].items() for k, v in m.items() if k in ("robustness_index", "seasonal_fidelity", "horizon_decay")}
        if deep:
            L.append("secondary: " + ", ".join(f"{k}={_num(v, '.3f')}" for k, v in deep.items()))
    if d["error"]:
        L.append(f"error: {_c(d['error'], 200)}")
    if d["edit_from_parent"]:
        L.append(f"derived from its parent by changing: {', '.join(_c(x, 40) for x in d['edit_from_parent'])}")
    if d["lineage"]:
        L.append("\nlineage (parent -> ... -> root):")
        for x in d["lineage"]:
            v = f"{x['value']:.4f}" if isinstance(x["value"], (int, float)) and not isinstance(x["value"], bool) else "-"
            L.append(f"  trial {x['trial']:>5}  value {v}  via {_c(x['source'], 60)}  edit {_c(x['edit'], 120)}")
    return "\n".join(L)
