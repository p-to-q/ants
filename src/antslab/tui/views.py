"""Screens as pure functions: data in, styled lines out. No curses, no I/O, no clock except what the data carries.

A line is a list of (text, style) spans; a style is a name from render.NAMED or a (fg, bg) pair of palette names (pixel art).
Every view returns `View(lines, rows)`: `rows` is how many selectable rows it has, so the loop can move a cursor without knowing the layout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import pixel

SCREENS = [("home", "Home"), ("overview", "Overview"), ("board", "Leaderboard"), ("trials", "Trials"), ("fleet", "Arms"),
           ("events", "Events"), ("health", "Health"), ("data", "Data"), ("system", "System")]
TAGLINE = "Autonomous Navigation of Tiny-model Space"
STATES = [None, "done", "failed", "timeout", "crashed", "queued", "running"]


@dataclass
class View:
    lines: list
    rows: int = 0
    row0: int = 0          # index of the line that carries row 0: lets the loop keep the selected row in sight


@dataclass
class Ctx:
    width: int = 100
    height: int = 30
    sel: int = 0
    tab: int = 0
    filt: dict = field(default_factory=dict)
    version: str = ""
    note: str = ""


def fit(lines, width: int):
    """cut every line to `width` cells, keeping the styles of what remains (a narrow terminal shows less, never wraps)"""
    out = []
    for line in lines:
        used, cut = 0, []
        for text, style in line:
            if used + len(text) <= width:
                cut.append((text, style)); used += len(text)
            else:
                if width - used > 0:
                    cut.append((text[: width - used], style))
                break
        out.append(cut)
    return out


def S(text, style="none"):
    return (text, style)


def clip(text, n):
    text = str(text)
    return text if len(text) <= n else text[: max(0, n - 1)] + "…"


def ago(s):
    if s is None:
        return "never"
    s = float(s)
    return f"{s:.0f}s" if s < 90 else f"{s / 60:.0f}m" if s < 5400 else f"{s / 3600:.1f}h" if s < 172800 else f"{s / 86400:.1f}d"


def rule(width, title=""):
    t = f"─ {title} " if title else ""
    return [S(t, "accent"), S("─" * max(0, width - len(t)), "dim")]


def cursor(i, sel, selectable=True):
    return S("▌" if (selectable and i == sel) else " ", "accent")


def gain(v):
    """rel_mse -> % better than the seasonal-naive reference (1.0)"""
    return None if v is None else (1.0 - float(v)) * 100


def fmt_v(v, nd=3):
    return "-" if v is None else f"{float(v):.{nd}f}"


def menu_lines(width):
    items = [("1", "Overview", "what is running now, rate, best curve"), ("2", "Leaderboard", "best models per size cap"),
             ("3", "Trials", "every evaluation; open one"), ("4", "Arms", "compare the running searches"),
             ("5", "Events", "the hash-chained record"), ("6", "Health", "doctor, integrity, backups"),
             ("7", "Data", "datasets and their roles"), ("8", "System", "version, service, spend, backups")]
    out = [rule(width, "Go to")]
    for k, name, what in items:
        out.append([S(f" {k} ", "key"), S(f"{name:<12}", "bold"), S(what, "dim")])
    out.append([S(" / ", "key"), S("command    ", "bold"), S("run any read-only ants command here (best, show 12, sql …)", "dim")])
    out.append([S(" ? ", "key"), S("help       ", "bold"), S("q quits", "dim")])
    return out


def fleet_table(rows, width, caps=None, selectable=False, sel=0):
    caps = caps or sorted({k for r in rows for k in r.get("best", {})}, key=lambda x: float(x))
    head = [S("  "), S(f"{'arm':<18}", "dim"), S(f"{'evals':>6}", "dim"), S(f"{'deep':>5}", "dim"), S(f"{'1h':>4}", "dim"), S(f"{'last':>6}  ", "dim")] + \
           [S(f"{'≤' + str(c):>10}", "dim") for c in caps]
    out = [head]
    for i, r in enumerate(rows):
        if "error" in r:
            out.append([cursor(i, sel, selectable), S(" "), S(f"{clip(r['arm'], 18):<18}", "bold"), S(clip(r["error"], width - 24), "bad")])
            continue
        age = r["last_age"]
        state = "bad" if not r["chain"] else "warn" if r["paused"] or (age or 0) > 3600 else "ok"
        line = [cursor(i, sel, selectable), S("● ", state), S(f"{clip(r['arm'], 18):<18}", "bold"), S(f"{r['evals']:>6}"), S(f"{r['deep']:>5}"), S(f"{r['h1']:>4}"),
                S(f"{ago(age):>6}  ", "dim")]
        for c in caps:
            b = r["best"].get(c)
            line.append(S(f"{fmt_v(b['value']) + '·' + b['stage']:>10}" if b else f"{'-':>10}", "none"))
        out.append(line)
    return out


# ---------------------------------------------------------------------------------------------------------------- screens
def home(d, ctx: Ctx) -> View:
    w = ctx.width
    logo = (pixel.side_by_side(pixel.ant_lines(), pixel.word_lines("ants", "mint_txt", None), gap=3) if w >= 80 else pixel.ant_lines())
    lines = [[]] + [[S("  ")] + l for l in logo]
    lines.append([S("  "), S(TAGLINE, "bold"), S(f"   {ctx.version}", "dim")])
    lines.append([])
    rows = d.get("fleet") or []
    if rows:
        lines.append(rule(w, "Running"))
        lines += fleet_table(rows, w)
        lines.append([S("  best value per size cap at the deepest stage reached; lower is better, 1.000 = seasonal naive", "dim")])
    else:
        lines.append([S("  No ledger found. `ants onboard` sets one up; `ants run` starts a search.", "warn")])
    lines.append([])
    lines += menu_lines(w)
    return View(lines)


def overview(d, ctx: Ctx) -> View:
    w = ctx.width
    s = d["snap"]
    lines = [[S(" "), S(f"{s['task']}", "accent"), S(f" · arm {s['arm']} · epoch {s['epoch']} · chain ", "dim"), S("ok" if s["chain_tail_ok"] else "BROKEN", "ok" if s["chain_tail_ok"] else "bad"),
              S(f" {s['head']}", "dim"), S("  PAUSED" if s["paused"] else "", "warn")]]
    lines.append([S(f" disk {s['free_gb']} GB free ({s['disk_level']}) · finished {s['finished_5min']} in 5 min, {s['finished_1h']} in 1 h"
                    + (f" · last {ago(s['last_finish_age_s'])} ago" if s["last_finish_age_s"] is not None else ""), "dim")])
    lines.append([])
    lines.append(rule(w, "Evaluations finished per 10 minutes, last 6 hours"))
    lines.append([S(" "), S(pixel.spark(s["rate_6h"]), "accent"), S(f"   max {max(s['rate_6h'] or [0])}", "dim")])
    if s["curve"]:
        lines.append([S(" "), S(pixel.spark(s["curve"], 60), "accent"), S(f"   best at deepest stage, {fmt_v(s['curve'][0])} → {fmt_v(s['curve'][-1])}", "dim")])
    lines.append([])
    lines.append(rule(w, "Stages"))
    for st, counts in sorted(s["stages"].items()):
        tot = sum(counts.values()) or 1
        done = counts.get("done", 0)
        lines.append([S(f" {st:<4}", "bold"), S(pixel.bar(done, tot, 24), "ok"), S(f" {done} done", "none")]
                     + [S(f"  {n} {state}", "warn" if state in ("failed", "timeout", "crashed") else "dim") for state, n in counts.items() if state != "done"])
    lines.append([])
    lines.append(rule(w, "Best per size cap (gain over seasonal naive)"))
    for cap, rows in s["best_by_cap"].items():
        if not rows:
            continue
        r = rows[0]
        g = gain(r["value"]) or 0
        lines.append([S(f" ≤{cap:>5} ", "bold"), S(pixel.bar(max(g, 0), 30, 20), "accent"), S(f" {g:5.1f}% ", "bold"), S(f"{fmt_v(r['value'], 4)} {r['stage']} ", "dim"),
                      S(f"{r['n_params']}p ", "none"), S(clip(f"#{r['trial']} {r['operator']}  {r['spec']}", max(10, w - 52)), "dim")])
    if s["operators"]:
        lines.append([])
        lines.append(rule(w, "Operators (discovery rate)"))
        ops = list(s["operators"].items())[:8]
        lines.append([S(" "), *[S(f"{k} {v['rate']:.2f}({v['n']})  ", "dim") for k, v in ops]])
    lc = s["llm_calls_1h"]
    if lc["calls"] or s["user_hints"]:
        lines.append([S(f" model calls last hour {lc['ok']}/{lc['calls']} ok", "dim")] + ([S("   hint: " + clip(s["user_hints"][-1], 60), "dim")] if s["user_hints"] else []))
    return View(lines)


def board(d, ctx: Ctx) -> View:
    w = ctx.width
    lb = d["lb"]
    caps = list(lb)
    if not caps:
        return View([[S(" no finished evaluations yet", "dim")]])
    tab = ctx.tab % len(caps)
    lines = [[S(" ")] + [S(f" ≤{c} ", "sel" if i == tab else "dim") if i != tab else S(f"[≤{c}]", "accent") for i, c in enumerate(caps)] + [S("   ←/→ cap · ↑/↓ row · Enter opens", "dim")], []]
    rows = lb[caps[tab]]
    head = f"  {'#':>2} {'value':>8} {'gain':>6}  {'':<14} {'params':>6} {'stage':<5} {'trial':>6} {'operator':<10} spec"
    lines.append([S(head, "dim")])
    for i, r in enumerate(rows):
        g = gain(r["value"]) or 0
        lines.append([cursor(i, ctx.sel), S(f" {r['rank']:>2} ", "dim"), S(f"{r['value']:>8.4f} ", "bold"), S(f"{g:5.1f}% ", "ok" if g > 0 else "warn"),
                      S(pixel.bar(max(g, 0), 30, 14), "accent"), S(f" {r['n_params']!s:>6} {r['stage']:<5} {r['trial']:>6} {clip(r['operator'], 10):<10} ", "none"),
                      S(clip(r["spec"], max(10, w - 62)), "dim")])
    return View(lines, len(rows), 3)


def trials(d, ctx: Ctx) -> View:
    w = ctx.width
    st, stg = ctx.filt.get("state"), ctx.filt.get("stage")
    lines = [[S(f" state: {st or 'all'}   stage: {stg or 'all'}", "accent"), S("   s = cycle state · t = cycle stage · Enter opens", "dim")], []]
    lines.append([S(f"  {'id':>6} {'stage':<5} {'state':<8} {'value':>8} {'cpu_s':>6} {'age':>6}  operator / error", "dim")])
    now = d.get("now", time.time())
    rows = d["rows"]
    for i, r in enumerate(rows):
        bad = r["state"] in ("failed", "timeout", "crashed")
        age = ago(now - r["finished_at"]) if r["finished_at"] else "-"
        lines.append([cursor(i, ctx.sel), S(f" {r['id']:>6} {r['stage']:<5} ", "none"), S(f"{r['state']:<8} ", "bad" if bad else "ok" if r["state"] == "done" else "dim"),
                      S(f"{fmt_v(r['value'], 4):>8} {fmt_v(r['cpu_s'], 1):>6} {age:>6}  ", "none"),
                      S(clip(r["error"] if bad and r["error"] else r["source"], max(10, w - 48)), "dim")])
    return View(lines, len(rows), 3)


def detail(d, ctx: Ctx) -> View:
    w = ctx.width
    t = d["detail"]
    if "not_found" in t:
        return View([[S(" " + t["not_found"], "warn")]])
    lines = [[S(f" trial {t['trial']} ", "accent"), S(f"{t['state']} · arm {t['arm']} · {t['source']} · epoch {t['epoch']} · key {t['spec_key']}", "dim")], []]
    lines.append(rule(w, "Levers that differ from the defaults"))
    lines.append([S(" " + t["non_default"], "none")])
    lines.append([])
    lines.append(rule(w, "Metrics by stage"))
    for stage, m in t["stages"].items():
        shown = [f"{k}={v:.4g}" for k, v in list(m.items())[:7] if isinstance(v, (int, float))]
        lines.append([S(f" {stage:<4}", "bold"), S(clip("  ".join(shown), w - 8), "none")])
    if t.get("error"):
        lines.append([]); lines.append([S(" error: ", "bad"), S(clip(t["error"], w - 10), "none")])
    if t["lineage"]:
        lines.append([]); lines.append(rule(w, "Lineage (parent first)"))
        for p in t["lineage"][:12]:
            lines.append([S(f"  #{p['trial']:<6}", "bold"), S(f"{fmt_v(p['value'], 4):>8}  {p['source']}  ", "dim"), S(clip(str(p["edit"]), w - 36), "dim")])
    lines.append([]); lines.append([S(" Esc back", "dim")])
    return View(lines)


def fleet_screen(d, ctx: Ctx) -> View:
    w = ctx.width
    rows = d["fleet"]
    lines = [[S(" Compare searches at equal evaluations, not equal time: arms run at different speeds.", "dim")], []]
    lines += fleet_table(rows, w, selectable=True, sel=ctx.sel)
    lines.append([])
    lines.append(rule(w, "Best-so-far curves (deepest stage)"))
    for r in rows:
        if "error" not in r and r["curve"]:
            lines.append([S(f" {clip(r['arm'], 18):<18}", "bold"), S(pixel.spark(r["curve"], 50), "accent"), S(f"  {fmt_v(r['curve'][-1])}", "dim")])
    lines.append([]); lines.append([S(" Enter switches to that search", "dim")])
    return View(lines, len(rows), 3)


def events(d, ctx: Ctx) -> View:
    w = ctx.width
    lines = [[S(f" kind: {ctx.filt.get('kind') or 'all'}", "accent"), S("   k = cycle kind (all, trial_finished, agent_tool, user_hint, restored)", "dim")], []]
    for e in d["rows"]:
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("seq", "time", "kind"))
        lines.append([S(f" {e['seq']:>7} ", "dim"), S(f"{e['time']} ", "dim"), S(f"{clip(e['kind'], 16):<16} ", "bold"), S(clip(extra, max(10, w - 46)), "none")])
    return View(lines, len(d["rows"]), 2)


def health(d, ctx: Ctx) -> View:
    style = {"OK": "ok", "WARN": "warn", "FAIL": "bad"}
    lines = [[S(" ")]]
    for level, msg in d["rows"]:
        lines.append([S(f" {level:<4} ", style.get(level, "none")), S(clip(msg, ctx.width - 8), "none")])
    n_bad = sum(1 for l, _ in d["rows"] if l == "FAIL")
    n_warn = sum(1 for l, _ in d["rows"] if l == "WARN")
    lines.insert(0, [S(" all clear" if not (n_bad or n_warn) else f" {n_bad} failing, {n_warn} warnings", "ok" if not (n_bad or n_warn) else "bad" if n_bad else "warn")])
    return View(lines)


def data_screen(d, ctx: Ctx) -> View:
    w = ctx.width
    if "error" in d:
        return View([[S(" " + d["error"], "warn")]])
    o = d["data"]
    lines = [[S(f" {o['base']}", "dim"), S(f"   raw {o['raw_bytes'] / 1e6:.0f} MB · processed {o['processed']} files", "dim")], []]
    meaning = {"dev": "steers the search", "confirm": "re-checks a frozen candidate", "sealed": "looked at once, at the end", "stress": "synthetic stress tests", "synthetic": "generated on demand"}
    for role in ("dev", "confirm", "sealed", "stress", "synthetic"):
        items = o["roles"].get(role)
        if not items:
            continue
        if role == "synthetic":
            lines.append([S(f" {role:<9}", "accent"), S(f"{len(items):>3} generated  ", "none"), S(meaning[role], "dim")])
            continue
        have = sum(1 for _, ok in items if ok)
        lines.append([S(f" {role:<9}", "accent"), S(f"{have:>3}/{len(items):<3} present  ", "ok" if have == len(items) else "warn"), S(meaning[role], "dim")])
        lines.append([S("    " + clip(", ".join(f"{n}{'' if ok else '✗'}" for n, ok in items), w - 6), "dim")])
    lines.append([]); lines.append([S(" `ants data fetch --tier extended` downloads more; every file is checked against the pinned hash", "dim")])
    return View(lines)


def system_screen(d, ctx: Ctx) -> View:
    i = d["info"]
    w = ctx.width
    lines = [[S(f" ants {i['version']}", "accent"), S(f"   latest known {i['latest'] or '-'}", "dim"), S("   update available: `ants update`" if i.get("update") else "", "warn")], []]
    kv = [("home", i["home"]), ("config", i["config"] or "(none; `ants onboard`)"), ("workdir", i.get("workdir", "-")), ("backups in", i.get("backup_dir", "-"))]
    if "service" in i:
        kv.append(("service", f"{i['service']['unit']}: {i['service']['state']}"))
    for k, v in kv:
        lines.append([S(f" {k:<10}", "dim"), S(clip(v, w - 14), "none")])
    if "llm_24h" in i:
        L = i["llm_24h"]
        lines.append([]); lines.append(rule(w, "Model spend, last 24 hours"))
        lines.append([S(" "), S(pixel.bar(L["calls"], L["cap"] or max(L["calls"], 1), 24), "accent"), S(f" {L['calls']} calls of {L['cap'] or 'no'} cap · {L['ok']} ok · {i.get('agent_tools_24h', 0)} agent tool reads", "dim")])
    bk = i.get("backups") or []
    lines.append([]); lines.append(rule(w, "Backups"))
    if not bk:
        lines.append([S(" none yet: `ants backup`, or `ants service install` for a daily one", "warn")])
    for m in bk[:6]:
        lines.append([S(f" {time.strftime('%m-%d %H:%M', time.localtime(m['created']))} ", "dim"), S(f"{m['file']:<40} ", "none"), S(f"{m['trials']} trials · {m['stored_bytes'] / 1e6:.1f} MB", "dim")])
    return View(lines)


def pager(text: str, ctx: Ctx, title: str = "") -> View:
    lines = [[S(f" {title}", "accent"), S("   ↑/↓ PgUp/PgDn scroll · Esc back", "dim")], []]
    for raw in text.splitlines() or [""]:
        lines.append([S(" " + raw[: ctx.width - 2], "none")])
    return View(lines, len(lines))


HELP = [
    ("1-8, 0", "go to a screen (0 = home)"), ("← →", "tabs (caps on the leaderboard) or previous/next screen"), ("↑ ↓  PgUp PgDn", "move / scroll"),
    ("Enter", "open the selected trial, or switch to the selected arm"), ("Esc  b", "back"), ("r", "refresh now (it refreshes by itself every 3 s)"),
    ("a / w", "next arm / next workdir"), ("s t k", "filters on Trials and Events"), ("/", "command: best, show 12, diff 5 7, sql SELECT …, log, doctor, hint …, pause, resume"),
    ("?", "this help"), ("q", "quit"),
]


def help_screen(ctx: Ctx) -> View:
    lines = [[S(" Keys", "accent")], []]
    for k, v in HELP:
        lines.append([S(f" {k:<16}", "key"), S(v, "none")])
    lines.append([]); lines.append([S(" Read-only by construction: the interface opens the ledger read-only. `hint`, `pause` and `resume` are the only commands that write, and they go through the normal CLI.", "dim")])
    return View(lines)
