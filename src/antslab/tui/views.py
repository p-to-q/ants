"""Screens as pure functions: data in, styled lines out. No curses, no I/O; the clock arrives in `ctx.t`, so animations are testable too.

A line is a list of (text, style) spans; a style is a name from render.NAMED or a (fg, bg) pair of palette names (pixel art).
Every view returns `View(lines, rows, row0)`: `rows` is how many selectable rows it has and `row0` the line of the first, so the loop can
move a cursor and keep it in sight without knowing the layout.
"""
from __future__ import annotations

import math
import time
import textwrap
from dataclasses import dataclass, field

from . import anim, pixel, vibe

SCREENS = [("home", "Home"), ("overview", "Overview"), ("board", "Leaderboard"), ("trials", "Trials"), ("fleet", "Arms"),
           ("events", "Events"), ("health", "Health"), ("data", "Data"), ("system", "System"), ("afterlife", "Afterlife")]
TAGLINE = "Autonomous Navigation of Tiny-model Space"
ONE_LINE = "A small autonomous research loop: it starts on one cheap machine and scales with the compute you give it."
INTRO = ["It proposes tiny forecasting models, tests each one with a fixed evaluator that nothing the searchers write can reach,",
         "and keeps a tamper-evident record of everything it tried. You are looking at it while it runs."]
STATES = [None, "done", "failed", "timeout", "crashed", "queued", "running"]
STATE_GLYPH = {"done": ("✓", "ok"), "failed": ("✗", "bad"), "timeout": ("⏱", "warn"), "crashed": ("✗", "bad"), "queued": ("·", "dim"), "running": ("▸", "accent")}
KEYS = {
    "home": "0-9 or ←→ screens · [ ] next/prev search · p pause/resume · h hint · / command · ? help · q quit",
    "overview": "←→ screens · r refresh · p pause/resume · h hint · [ ] search · space freeze · ? help",
    "board": "Tab/< > cap · ←→ screens · ↑↓ row · Enter open · p pause · h hint · [ ] search · ? help",
    "trials": "↑↓ row · Enter open · s state · t stage · g/G top/bottom · space freeze · ? help",
    "fleet": "↑↓ row · Enter switch to that search · p pause/resume it · h hint · ? help",
    "events": "↑↓ scroll · k kind · space freeze · ? help",
    "health": "r refresh · ? help",
    "data": "r refresh · ? help",
    "system": "r refresh · ? help",
    "afterlife": "↑↓ round · Enter evidence / parameters · ←→ screens · r refresh cache · ? help",
    "detail": "Esc back · ? help",
}


@dataclass
class View:
    lines: list
    rows: int = 0
    row0: int = 0          # index of the line that carries row 0


@dataclass
class Ctx:
    width: int = 100
    height: int = 30
    sel: int = 0
    tab: int = 0
    filt: dict = field(default_factory=dict)
    version: str = ""
    note: str = ""
    t: float = 0.0                       # clock for animations (seconds); 0 means 'a still frame'
    ascii_only: bool = False
    prev_ranks: dict = field(default_factory=dict)


def S(text, style="none"):
    return (text, style)


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


def spin(name, ctx):
    return anim.frame(name, ctx.t, ctx.ascii_only, still=(ctx.t == 0)) if name else "●"


def mood_line(m, ctx, prefix=" "):
    """the plain-language line: an animated glyph, the phrase in its colour, the reason in grey"""
    return [S(prefix), S(spin(m["glyph"], ctx) if m.get("glyph") else "●", m["style"]), S(" "), S(m["phrase"], m["style"]), S(f": {m['reason']}", "dim")]


def mood_of(inputs) -> dict:
    m = vibe.mood(inputs)
    return {"phrase": m.phrase, "style": m.style, "reason": m.reason, "glyph": m.glyph}


def heat(values, width):
    vmax = max(values or [0])
    return "".join(pixel.shade(v, vmax) for v in values[-width:])


def menu_lines(width):
    items = [("1", "Overview", "what is running now, the chart of the best so far"), ("2", "Leaderboard", "the best models per size cap"),
             ("3", "Trials", "every evaluation; open one"), ("4", "Arms", "compare the running searches at equal budget"),
             ("5", "Events", "the hash-chained record"), ("6", "Health", "doctor, integrity, backups"),
             ("7", "Data", "datasets and their roles"), ("8", "System", "version, service, model spend, backups"),
             ("9", "Afterlife", "past experiments and retained evidence")]
    out = [rule(width, "Go to")]
    for k, name, what in items:
        out.append([S(f" {k} ", "key"), S(f"{name:<12}", "bold"), S(what, "dim")])
    out.append([S(" p ", "key"), S("pause       ", "bold"), S("pause or resume the search you are looking at", "dim")])
    out.append([S(" h ", "key"), S("hint        ", "bold"), S("tell the searchers something (a suggestion, never a rule)", "dim")])
    out.append([S(" / ", "key"), S("command     ", "bold"), S("best, show 12, diff 5 7, sql SELECT …, doctor, log …", "dim")])
    return out


def fleet_table(rows, width, ctx: Ctx, caps=None, selectable=False, sel=0, wide=True):
    caps = caps or sorted({k for r in rows for k in r.get("best", {})}, key=lambda x: float(x))
    spark_w = 18 if wide and width >= 112 else 0
    head = [S("   "), S(f"{'search':<18}", "dim"), S(f"{'evals':>6}", "dim"), S(f"{'deep':>5}", "dim"), S(f"{'1h':>4}", "dim"), S(f"{'last':>6}  ", "dim")] + \
           [S(f"{'≤' + str(c):>10}", "dim") for c in caps] + ([S("  last 6 hours", "dim")] if spark_w else [])
    out = [head]
    for i, r in enumerate(rows):
        if "error" in r:
            out.append([cursor(i, sel, selectable), S("  "), S(f"{clip(r['arm'], 18):<18}", "bold"), S(clip(r["error"], width - 24), "bad")])
            continue
        m = r["mood"]
        mm = mood_of(m)
        line = [cursor(i, sel, selectable), S(spin(mm["glyph"], ctx) if mm["glyph"] else "●", mm["style"]), S(" "), S(f"{clip(r['arm'], 18):<18}", "bold"),
                S(f"{r['evals']:>6}"), S(f"{r['deep']:>5}"), S(f"{r['h1']:>4}"), S(f"{ago(r['last_age']):>6}  ", "dim")]
        for c in caps:
            b = r["best"].get(c)
            line.append(S(f"{fmt_v(b['value']) + '·' + b['stage']:>10}" if b else f"{'-':>10}", "none"))
        if spark_w:
            line += [S("  "), S(pixel.spark(r["rate_6h"], spark_w), "accent")]
        out.append(line)
        out.append([S("   "), S("  "), S(mm["phrase"], mm["style"]), S(f"  {clip(mm['reason'], max(10, width - 30))}", "dim")])
    return out


# ---------------------------------------------------------------------------------------------------------------- screens
def home(d, ctx: Ctx) -> View:
    w = ctx.width
    ant = pixel.ant_lines(anim.pulse(ctx.t))
    chip = S(f" {ctx.version} ", ("ink", "mint")) if ctx.version else S("")
    if w >= 84:
        right = [[]] + pixel.word_lines("ants", "mint_txt", None) + [[], [S(TAGLINE, "bold"), S("  "), chip]]       # the word beside the ant, the name under the word
        logo = pixel.side_by_side(ant, right, gap=3, valign="top")
        lines = [[]] + [[S("  ")] + l for l in logo]
    else:
        lines = [[]] + [[S("  ")] + l for l in ant] + [[], [S("  "), S(TAGLINE, "bold"), S("  "), chip]]
    lines.append([])                                                       # air between the picture and the words
    lines.append([S("  " + ONE_LINE, "none")])
    for l in INTRO:
        lines.append([S("  " + l, "dim")])
    rows0 = [r for r in (d.get("fleet") or []) if "error" not in r]
    if rows0:
        found = sum(1 for r in rows0 if mood_of(r["mood"])["phrase"] == "Just found something")
        ok = sum(1 for r in rows0 if mood_of(r["mood"])["style"] in ("ok", "accent"))
        lines.append([S("  "), S(spin("alive", ctx), "accent"), S(f" {len(rows0)} searches · {ok} doing well" + (f" · {found} just found something" if found else ""), "dim")])
    lines.append([])
    rows = d.get("fleet") or []
    if rows:
        lines.append(rule(w, "Running"))
        lines += fleet_table(rows, w, ctx)
        lines.append([S("  value = rel_mse at the deepest stage reached, lower is better, 1.000 = seasonal naive", "dim")])
    else:
        lines.append([S("  No ledger found. `ants onboard` sets one up; `ants run` starts a search.", "warn")])
    lines.append([])
    lines += menu_lines(w)
    return View(lines)


def _braille(values, width, height, lo=None, hi=None, style="accent"):
    return [[S(" "), S(r, style)] for r in pixel.braille_plot(values, width, height, lo, hi)]


def overview(d, ctx: Ctx) -> View:
    w = ctx.width
    s = d["snap"]
    m = s["mood"]
    mm = mood_of(m)
    lines = [[S(" "), S(f"{s['task']}", "accent"), S(f" · {s['arm']} · epoch {s['epoch']} · chain ", "dim"), S("ok" if s["chain_tail_ok"] else "BROKEN", "ok" if s["chain_tail_ok"] else "bad"),
              S(f" {s['head']}", "dim")], mood_line(mm, ctx)]
    lines.append([S(f" disk {s['free_gb']} GB free · {s['finished_5min']} finished in 5 min, {s['finished_1h']} in 1 h"
                    + (f" · last {ago(s['last_finish_age_s'])} ago" if s["last_finish_age_s"] is not None else ""), "dim")])
    run = s.get("running") or []
    if run:
        lines.append([])
        lines.append(rule(w, "Working on now"))
        for r in run[:4]:
            lines.append([S(" "), S(spin("running", ctx), "accent"), S(f" #{r['id']} {r['stage']} ", "bold"), S(f"{r['source']}", "none"), S(f"  {ago(r['for_s'])}" if r["for_s"] is not None else "", "dim")])
    lines.append([])
    cw = max(10, min(70, w - 22))
    if s["curve"]:
        lo, hi = min(s["curve"]), max(s["curve"])
        lines.append(rule(w, "Best so far at the deepest stage"))
        for i, row in enumerate(_braille(s["curve"], cw, 4)):
            label = f"{hi:.3f}" if i == 0 else f"{lo:.3f}" if i == 3 else ""
            lines.append(row + [S(f" {label}", "dim")])
        lines.append([S(f"  {fmt_v(s['curve'][0])} → {fmt_v(s['curve'][-1])} over {len(s['curve'])} points; lower is better", "dim")])
        lines.append([])
    lines.append(rule(w, "Evaluations finished, per 10 minutes, last 6 hours"))
    lines.append([S(" "), S(heat(s["rate_6h"], 36), "accent"), S("  "), S(pixel.spark(s["rate_6h"], 36), "dim"), S(f"   max {max(s['rate_6h'] or [0])}", "dim")])
    lines.append([])
    lines.append(rule(w, "Stages"))
    for st, counts in sorted(s["stages"].items()):
        tot = sum(counts.values()) or 1
        done, running, bad = counts.get("done", 0), counts.get("running", 0), sum(n for k, n in counts.items() if k in ("failed", "timeout", "crashed"))
        queued = counts.get("queued", 0)
        bw = 28
        nd, nr, nb = (round(x / tot * bw) for x in (done, running, bad))
        bar = [S("█" * nd, "ok"), S("▒" * nr, "accent"), S("░" * nb, "bad"), S("·" * max(0, bw - nd - nr - nb), "dim")]
        lines.append([S(f" {st:<4}", "bold")] + bar + [S(f" {done} done", "none")] + ([S(f"  {running} running", "accent")] if running else []) + ([S(f"  {queued} queued", "dim")] if queued else [])
                     + ([S(f"  {bad} failed", "bad")] if bad else []))
    lines.append([])
    lines.append(rule(w, "Best per size cap (gain over seasonal naive)"))
    for cap, rows in s["best_by_cap"].items():
        if not rows:
            continue
        r = rows[0]
        g = gain(r["value"]) or 0
        lines.append([S(f" ≤{cap:>5} ", "bold"), S(pixel.bar(max(g, 0), 30, 20), "accent"), S(f" {g:5.1f}% ", "bold" if g > 0 else "warn"), S(f"{fmt_v(r['value'], 4)} {r['stage']} ", "dim"),
                      S(f"{r['n_params']}p ", "none"), S(clip(f"#{r['trial']} {r['operator']}  {r['spec']}", max(10, w - 52)), "dim")])
    if s["operators"]:
        lines.append([])
        lines.append(rule(w, "Searchers (share of their proposals that were worth a deeper look)"))
        for k, v in list(s["operators"].items())[:7]:
            lines.append([S(f" {clip(k, 12):<12}", "bold"), S(pixel.bar(v["rate"], 1.0, 16), "accent"), S(f" {v['rate']:.2f}  of {v['n']}", "dim")])
    ch = s.get("calls_hour") or []
    if any(a or b for a, b in ch):
        lines.append([])
        lines.append(rule(w, "Model calls per hour, last 24 hours (bright = ok, faint = failed)"))
        lines.append([S(" "), S(pixel.spark([a for a, _ in ch], 24), "accent"), S("  "), S(pixel.spark([b for _, b in ch], 24), "bad"),
                      S(f"   {s['llm_calls_1h']['ok']}/{s['llm_calls_1h']['calls']} ok this hour", "dim")])
    if s["user_hints"]:
        lines.append([S(" hint: " + clip(s["user_hints"][-1], w - 10), "dim")])
    return View(lines)


def board(d, ctx: Ctx) -> View:
    w = ctx.width
    lb = d["lb"]
    caps = list(lb)
    if not caps:
        return View([[S(" no finished evaluations yet", "dim")]])
    tab = ctx.tab % len(caps)
    lines = [[S(" ")] + [S(f"[≤{c}]", "accent") if i == tab else S(f" ≤{c} ", "dim") for i, c in enumerate(caps)] + [S("   Tab or < > cap · ←/→ screen · ↑/↓ row · Enter opens", "dim")], []]
    rows = lb[caps[tab]]
    sizes = [r["n_params"] for r in rows if r["n_params"]]
    lo, hi = (min(sizes), max(sizes)) if sizes else (1, 2)
    lw = 12
    lines.append([S(f"  {'#':>2} {'value':>8} {'gain':>6}  {'':<14} {'params (log)':<{lw}} {'stage':<5} {'trial':>6} {'':<2}{'operator':<10} spec", "dim")])
    for i, r in enumerate(rows):
        g = gain(r["value"]) or 0
        col = pixel.log_position(r["n_params"] or 0, lo, hi, lw)
        loll = "".join("●" if c == col else "─" if c < col else " " for c in range(lw))
        prev = ctx.prev_ranks.get(r["trial"])
        trend = "  " if prev is None else "▲ " if prev > r["rank"] else "▼ " if prev < r["rank"] else "= "
        lines.append([cursor(i, ctx.sel), S(f" {r['rank']:>2} ", "dim"), S(f"{r['value']:>8.4f} ", "bold"), S(f"{g:5.1f}% ", "ok" if g > 0 else "warn"),
                      S(pixel.bar(max(g, 0), 30, 14), "accent"), S(" "), S(loll, "accent"), S(f" {r['stage']:<5} {r['trial']:>6} ", "none"),
                      S(trend, "ok" if trend.startswith("▲") else "warn" if trend.startswith("▼") else "dim"), S(f"{clip(r['operator'], 10):<10} ", "none"),
                      S(clip(r["spec"], max(10, w - 80)), "dim")])
    lines.append([]); lines.append([S(f"  ● marks the parameter count between the smallest ({lo}) and the largest ({hi}) in this list", "dim")])
    return View(lines, len(rows), 3)


def trials(d, ctx: Ctx) -> View:
    w = ctx.width
    st, stg = ctx.filt.get("state"), ctx.filt.get("stage")
    rows = d["rows"]
    lines = [[S(f" state: {st or 'all'}   stage: {stg or 'all'}", "accent"), S("   s = cycle state · t = cycle stage · Enter opens", "dim")]]
    ribbon = [S(" "), S("newest left: ", "dim")]
    for r in rows[:max(10, w - 24)]:
        g, style = STATE_GLYPH.get(r["state"], ("?", "dim"))
        ribbon.append(S("▮", style))
    lines.append(ribbon)
    lines.append([S(f"    {'id':>6} {'stage':<5} {'state':<9} {'value':>8} {'cpu':<10} {'age':>5}  operator / error", "dim")])
    now = d.get("now", time.time())
    cmax = max([r["cpu_s"] or 0 for r in rows] or [1]) or 1
    for i, r in enumerate(rows):
        g, style = STATE_GLYPH.get(r["state"], ("?", "dim"))
        bad = r["state"] in ("failed", "timeout", "crashed")
        age = ago(now - r["finished_at"]) if r["finished_at"] else "-"
        cpu = r["cpu_s"] or 0
        dur = pixel.bar(math.log1p(cpu), math.log1p(cmax), 8)
        lines.append([cursor(i, ctx.sel), S(" "), S(g, style), S(f" {r['id']:>6} {r['stage']:<5} ", "none"), S(f"{r['state']:<9}", style),
                      S(f"{fmt_v(r['value'], 4):>8} ", "none"), S(dur, "dim"), S(f" {age:>5}  ", "dim"),
                      S(clip(r["error"] if bad and r["error"] else r["source"], max(10, w - 52)), "dim")])
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
    prim = None
    for stage, m in t["stages"].items():
        shown = [f"{k}={v:.4g}" for k, v in list(m.items())[:7] if isinstance(v, (int, float))]
        first = next(((k, v) for k, v in m.items() if isinstance(v, (int, float))), None)
        bar = pixel.bar(max(0.0, 1.5 - float(first[1])), 1.0, 16) if first and first[0].startswith("rel") else ""
        lines.append([S(f" {stage:<4}", "bold"), S(bar, "accent"), S(" "), S(clip("  ".join(shown), w - 28), "none")])
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
    lines = [[S(" Compare searches at equal evaluations, not equal time: they run at different speeds.", "dim")], []]
    lines += fleet_table(rows, w, ctx, selectable=True, sel=ctx.sel, wide=False)
    good = [r for r in rows if "error" not in r]
    if good:
        mx = max(r["deep"] for r in good) or 1
        eq = d.get("equal")
        n = eq["n"] if eq else 0
        lines.append([])
        lines.append(rule(w, "Deepest-stage evaluations so far (│ = the equal-budget cut)"))
        bw = max(10, min(44, w - 34))
        cut = int(round(n / mx * bw)) if n else None
        for r in good:
            cells = list(pixel.bar(r["deep"], mx, bw).ljust(bw))
            if cut is not None and 0 <= cut < bw:
                cells[cut] = "│" if cells[cut] == " " else cells[cut]
            lines.append([S(f" {clip(r['arm'], 18):<18}", "bold"), S("".join(cells), "accent"), S(f" {r['deep']}", "dim")])
        lines.append([])
        lines.append(rule(w, "Best-so-far curves (deepest stage)"))
        lo = min((min(r["curve"]) for r in good if r["curve"]), default=0)
        hi = max((max(r["curve"]) for r in good if r["curve"]), default=1)
        for r in good:
            if r["curve"]:
                lines.append([S(f" {clip(r['arm'], 18):<18}", "bold"), S(pixel.braille_plot(r["curve"], max(10, min(48, w - 40)), 1, lo, hi)[0], "accent"), S(f"  {fmt_v(r['curve'][-1])}", "dim")])
        ops = sorted({k for r in good for k in (r.get("ops") or {})}, key=lambda k: -sum((r.get("ops") or {}).get(k, {}).get("n", 0) for r in good))[:7]
        if ops:
            lines.append([])
            lines.append(rule(w, "Who proposed what (cells show how many proposals; darker = more)"))
            lines.append([S(f" {'':<18}", "dim")] + [S(f"{clip(k, 7):>8}", "dim") for k in ops])
            for r in good:
                mxn = max((r.get("ops") or {}).get(k, {}).get("n", 0) for k in ops) or 1
                lines.append([S(f" {clip(r['arm'], 18):<18}", "bold")] + [S(f"{pixel.shade((r.get('ops') or {}).get(k, {}).get('n', 0), mxn) * 3:>8}", "accent") for k in ops])
    eq = d.get("equal")
    if eq and eq["n"]:
        lines.append([])
        lines.append(rule(w, f"At equal budget: each search cut at its first {eq['n']} deepest-stage evaluations"))
        caps = sorted({k for r in eq["rows"] for k in r["best"]}, key=lambda x: float(x) if x != "all" else 0)
        lines.append([S(f" {'search':<18}", "dim"), S(f"{'evals':>6}", "dim")] + [S(f"{'≤' + c:>10}", "dim") for c in caps])
        for r in eq["rows"]:
            lines.append([S(f" {clip(r['arm'], 18):<18}", "bold"), S(f"{r['n']:>6}", "dim")] + [S(f"{fmt_v(r['best'].get(c)):>10}", "none") for c in caps])
        lines.append([S(" lower is better; one seed per search, so only large gaps mean anything", "dim")])
    lines.append([]); lines.append([S(" Enter switches to that search", "dim")])
    return View(lines, len(rows) * 2 and len(rows), 3)


KIND_STYLE = {"trial_finished": "dim", "agent_tool": "accent", "user_hint": "ok", "restored": "warn", "candidate_frozen": "warn"}


def events(d, ctx: Ctx) -> View:
    w = ctx.width
    lines = [[S(f" kind: {ctx.filt.get('kind') or 'all'}", "accent"), S("   k = cycle kind (all, trial_finished, agent_tool, user_hint, restored)", "dim")]]
    dens = d.get("per_minute") or []
    if dens:
        lines.append([S(" "), S(pixel.spark(dens, 60), "accent"), S(f"  events per minute, last hour (max {max(dens)})", "dim")])
    lines.append([])
    for e in d["rows"]:
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("seq", "time", "kind"))
        style = KIND_STYLE.get(e["kind"], "none")
        lines.append([S("▌", style), S(f" {e['seq']:>7} ", "dim"), S(f"{e['time']} ", "dim"), S(f"{clip(e['kind'], 16):<16} ", "bold"), S(clip(extra, max(10, w - 46)), "none")])
    return View(lines, len(d["rows"]), 3 if dens else 2)


TILE = {"OK": ("■", "ok"), "WARN": ("▲", "warn"), "FAIL": ("✗", "bad")}


def health(d, ctx: Ctx) -> View:
    w = ctx.width
    rows = d["rows"]
    n_bad = sum(1 for l, _ in rows if l == "FAIL")
    n_warn = sum(1 for l, _ in rows if l == "WARN")
    lines = [[S(" all clear" if not (n_bad or n_warn) else f" {n_bad} failing, {n_warn} warnings", "ok" if not (n_bad or n_warn) else "bad" if n_bad else "warn")]]
    strip = [S(" ")]
    for level, _ in rows:
        g, style = TILE.get(level, ("?", "dim"))
        strip += [S(g, style), S(" ", "none")]
    lines.append(strip)
    lines.append([])
    for level, msg in rows:
        g, style = TILE.get(level, ("?", "dim"))
        lines.append([S(f" {g} ", style), S(f"{level:<4} ", style), S(clip(msg, w - 10), "none")])
    ex = d.get("extra") or {}
    if ex:
        lines.append([])
        lines.append(rule(w, "Gauges"))
        if ex.get("backup_age_h") is not None:
            a = ex["backup_age_h"]
            lines.append([S(f" backup age   ", "dim"), S(pixel.bar(min(a, 48), 48, 24), "ok" if a <= 36 else "warn"), S(f" {a:.1f} h (a day is half the bar)", "dim")])
        if ex.get("disk_used_frac") is not None:
            f = ex["disk_used_frac"]
            lines.append([S(f" disk used    ", "dim"), S(pixel.bar(f, 1, 24), "ok" if f < 0.6 else "warn" if f < 0.8 else "bad"), S(f" {f:.0%}", "dim")])
        if ex.get("ledger_mb") is not None:
            lines.append([S(f" ledger size  ", "dim"), S(pixel.bar(math.log1p(ex["ledger_mb"]), math.log1p(1000), 24), "accent"), S(f" {ex['ledger_mb']:.1f} MB", "dim")])
    return View(lines)


def data_screen(d, ctx: Ctx) -> View:
    w = ctx.width
    if "error" in d:
        return View([[S(" " + clip(t, w - 2), "warn" if i == 0 else "dim")] for i, t in enumerate(d["error"].split("\n"))])
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
        tiles = [S("■" if ok else "□", "ok" if ok else "warn") for _, ok in items[:60]]
        lines.append([S(f" {role:<9}", "accent")] + tiles + [S(f"  {have}/{len(items)} present  ", "ok" if have == len(items) else "warn"), S(meaning[role], "dim")])
        lines.append([S("    " + clip(", ".join(f"{n}{'' if ok else '✗'}" for n, ok in items), w - 6), "dim")])
    lines.append([]); lines.append([S(" `ants data fetch --tier extended` downloads more; every file is checked against the pinned hash. ■ present, □ missing", "dim")])
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
        frac = (L["calls"] / L["cap"]) if L["cap"] else 0
        lines.append([S(" "), S(pixel.bar(L["calls"], L["cap"] or max(L["calls"], 1), 24), "ok" if frac < 0.7 else "warn" if frac < 1 else "bad"),
                      S(f" {L['calls']} calls of {L['cap'] or 'no'} cap · {L['ok']} ok · {i.get('agent_tools_24h', 0)} agent tool reads", "dim")])
        per = i.get("llm_by_ledger") or []
        if len(per) > 1:
            tot = sum(x["calls"] or 0 for x in per)
            lines.append([S(f" this search above · all {len(per)} ledgers together: {tot} calls (the cap of {L['cap'] or 'none'} applies to each ledger on its own)", "dim")])
            for x in per:
                lines.append([S(f"   {clip(x['name'], 30):<30} ", "none"), S("unreadable" if x["calls"] is None else f"{x['calls']} calls · {x['ok']} ok", "dim")])
        if i.get("llm_cap_source"):
            lines.append([S(" cap from: " + clip(i["llm_cap_source"], w - 12), "dim")])
        ch = i.get("calls_hour") or []
        if any(a or b for a, b in ch):
            lines.append([S(" "), S(pixel.spark([a for a, _ in ch], 24), "accent"), S("  "), S(pixel.spark([b for _, b in ch], 24), "bad"), S("   per hour; second line = failed", "dim")])
        # per-endpoint detail from llm_summary
        summ = i.get("llm_summary")
        ep_states = i.get("llm_ep_states") or {}
        if summ and summ.get("endpoints"):
            lines.append([]); lines.append(rule(w, "Per endpoint"))
            for ep_name, ep_d in sorted(summ["endpoints"].items()):
                att, eok = ep_d["attempts"], ep_d["ok"]
                rate = f"{eok / att:.0%}" if att else "-"
                causes = ep_d.get("causes", {})
                top3 = sorted(causes.items(), key=lambda kv: -kv[1])[:3]
                cause_str = ", ".join(f"{k}:{v}" for k, v in top3) if top3 else ""
                st = ep_states.get(ep_name, {})
                q_str = ""
                if st.get("state") == "quarantined":
                    until = st.get("until")
                    if until and until > (i.get("now") or time.time()):
                        q_str = f" QUARANTINED until {time.strftime('%H:%M', time.localtime(until))}"
                lines.append([S(f" {clip(ep_name, 12):<12}", "bold"),
                              S(f" {att:>5} calls {rate:>4} ok", "dim"),
                              S(f"  {clip(cause_str, max(10, w - 42))}" if cause_str else "", "dim"),
                              S(q_str, "warn") if q_str else S("", "none")])
    bk = i.get("backups") or []
    lines.append([]); lines.append(rule(w, "Backups (7 days)"))
    if not bk:
        lines.append([S(" none yet: `ants backup`, or `ants service install` for a daily one", "warn")])
    else:
        now = i.get("now") or time.time()
        axis = [" "] * 56
        for m in bk:
            pos = 55 - int((now - m["created"]) / (7 * 86400) * 55)
            if 0 <= pos < 56:
                axis[pos] = "●"
        lines.append([S(" "), S("".join(axis), "accent"), S("  oldest ← → now", "dim")])
    if bk and i.get("trials_now") is not None and bk[0].get("trials") is not None:
        gap = i["trials_now"] - bk[0]["trials"]
        age = (i.get("now") or time.time()) - bk[0]["created"]
        lines.append([S(clip(f" restoring the newest backup would lose {max(gap, 0)} trials ({age / 3600:.0f} h of work)", w - 2), "warn" if gap > 100 or age > 86400 else "dim")])
        lines.append([S(clip(" copies on this machine guard against mistakes, not against losing the machine: `deploy/remote_deploy.sh pull-backups`", w - 2), "dim")])
    for m in bk[:6]:
        lines.append([S(f" {time.strftime('%m-%d %H:%M', time.localtime(m['created']))} ", "dim"), S(f"{m['file']:<40} ", "none"), S(f"{m['trials']} trials · {m['stored_bytes'] / 1e6:.1f} MB", "dim")])
    return View(lines)


def afterlife_screen(d, ctx: Ctx) -> View:
    from ..textsafe import clean_text
    catalog = d.get("catalog", {})
    rounds = catalog.get("rounds", [])
    w = ctx.width
    def prose(text, style="dim"):
        return [[S(" " + part, style)] for part in textwrap.wrap(text, max(10, w - 2))]
    lines = [[S(" Afterlife", "accent")]]
    lines += prose("Experiments end, but their findings and evidence remain useful. Revisit past rounds here, inspect their results, and find the verified copies that preserve the work.")
    lines += [[S(f" {len(rounds)} retained {'round' if len(rounds) == 1 else 'rounds'}. This view reads cached records only.", "dim")], []]
    if catalog.get("error"):
        lines += prose("The index could not be read. Check its format and permissions, or rebuild it from verified bundles. No running experiment was changed.", "warn")
        lines += prose(clean_text(catalog["error"], 200), "warn")
    if not rounds:
        if not catalog.get("error"):
            lines += prose("No past rounds have been indexed yet. Once a round has been sealed and verified, its summary can be added here.")
        lines += prose("Run `ants archive index --help` for the indexing command. The index is a cache; the sealed bundle remains the evidence.")
        return View(lines)
    selected = min(max(ctx.sel, 0), len(rounds) - 1)
    count = min(7, max(2, ctx.height - 27))
    start = (selected // count) * count
    label_w = max(16, w - 43)
    lines.append([S(f" {'date':<11}{'round / question':<{label_w}}{'kind':<11}receipt", "dim")])
    row0 = len(lines) - start
    for i in range(start, min(start + count, len(rounds))):
        r = rounds[i]
        label = clean_text(r.get("title", r.get("id", "?")), 120)
        date = clean_text(str(r.get("created_utc", "?"))[:10], 11)
        kind = clean_text(r.get("kind", "?"), 10)
        lines.append([cursor(i, selected), S(f"{date:<11}", "dim"),
                      S(f"{clip(label, label_w):<{label_w}}", "bold" if i == selected else "none"),
                      S(f"{kind:<11}", "dim"), S("off-host*" if r.get("offsite_note") else "local*", "ok" if r.get("offsite_note") else "warn")])
    lines.append([S(f" {start + 1}-{min(start + count, len(rounds))} / {len(rounds)} · ↑↓ PgUp/PgDn select", "dim")])
    row = rounds[selected]
    lines += [[], rule(w, clean_text(row.get("title", row["id"]), 100))]
    if row.get("kind") == "snapshot":
        lines += prose("This is an operational checkpoint. Research results are intentionally omitted while the experiment may still be running.")
    else:
        direction = "lower" if row.get("direction") == "min" else "higher"
        lines.append([S(f" {clean_text(row.get('metric', '?'), 40)} · {row.get('stage', '?')} · cap ≤{row.get('focus_cap', '?')} · {direction} is better", "dim")])
        lines.append([S(" Descriptive medians of per-run best values. n/N reports coverage.", "dim")])
        preview = row.get("preview", [])
        limit = min(6, max(1, ctx.height - 22))
        for result in preview[:limit]:
            lines.append([S(f" {clip(clean_text(result.get('arm', '?'), 40), 24):<24}", "none"),
                          S(f" {fmt_v(result.get('median'), 5):>10}", "accent"),
                          S(f"  {result.get('n', 0)}/{result.get('total', 0)}  epoch {clean_text(result.get('epoch', '?'), 12)}", "dim")])
        if any(result.get("n", 0) < result.get("total", 0) for result in preview):
            lines += prose("Some runs have no eligible result. Missing values are excluded, not treated as zero.", "warn")
        if row.get("kind") == "pilot":
            lines += prose("These pilot results are exploratory, not confirmatory.", "warn")
        if len(preview) > limit:
            lines.append([S(f" {len(preview) - limit} more arm summaries are available with Enter.", "dim")])
    checked = row.get("verified_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(checked)) if isinstance(checked, (float, int)) else "not recorded"
    lines += [[S(f" Verified {when}. Receipts are recorded, not checked live.", "dim")],
              [S(" Off-host receipt: " + clean_text(row.get("offsite_note") or "not recorded", 160), "none")],
              [S(" Enter opens the full results, parameters and recovery locations.", "dim")]]
    return View(lines, len(rounds), row0)


def afterlife_detail(summary: dict, width: int) -> str:
    """Human-first evidence receipt, followed by the complete machine-readable record."""
    import json
    from ..textsafe import clean_text
    snapshot = summary.get("kind") == "snapshot"
    lines = [str(summary.get("title", summary.get("id", "Round"))), "",
             "WHAT WAS PRESERVED",
             f"Round: {summary.get('id')} ({summary.get('kind')})",
             f"Task: {summary.get('task')} | runs: {len(summary.get('runs', []))}",
             "Results omitted: operational checkpoint." if snapshot else summary.get("summary_policy", "Descriptive results only."),
             "", "WHERE TO FIND THE EVIDENCE",
             f"Bundle: {summary.get('bundle_location', 'not recorded')}",
             f"SHA-256: {summary.get('bundle_sha256', 'not recorded')}",
             f"Off-host receipt: {summary.get('offsite_note') or 'not recorded'}",
             "Receipts record the last verification, not current file availability.",
             "", "SAFE NEXT STEPS",
             "Locate the bundle and verify it with `ants bundle verify PATH`.",
             "For recovery, restore into a separate directory and check the chain before use.",
             "Nothing on this page restores, deletes or changes a running experiment.",
             "", "FULL RECORD (params, trial IDs, epochs, chain heads and cap results)"]
    lines += json.dumps(summary, indent=2, ensure_ascii=True).splitlines()
    return "\n".join(part for line in lines for part in
                     (textwrap.wrap(clean_text(line, 10000), max(20, width - 4),
                                    replace_whitespace=False, drop_whitespace=False) or [""]))


def pager(text: str, ctx: Ctx, title: str = "") -> View:
    lines = [[S(f" {title}", "accent"), S("   ↑/↓ PgUp/PgDn scroll · Esc back", "dim")], []]
    for raw in text.splitlines() or [""]:
        lines.append([S(" " + raw[: ctx.width - 2], "none")])
    return View(lines, len(lines))


HELP = [
    ("1-9, 0", "go to a screen (0 = home)"), ("← →", "tabs (caps on the leaderboard) or previous/next screen"), ("↑ ↓  PgUp PgDn  g G", "move / scroll / top / bottom"),
    ("Enter", "open the selected trial, or switch to the selected search"), ("Esc  b", "back"), ("r", "refresh now (it refreshes by itself every 3 s)"),
    ("space", "freeze or unfreeze the refresh (the age tag turns yellow)"), ("[  ]", "previous / next search (a ledger and its arm)"),
    ("p", "pause or resume the search you are looking at (the PAUSE flag; the loop finishes its step and idles)"), ("h", "write a hint for the searchers (a suggestion; hints never change gates or budgets)"),
    ("a / w", "next arm / next workdir"), ("s t k", "filters on Trials and Events"),
    ("/", "command: best, show 12, diff 5 7, sql SELECT …, log, doctor, status, backups, hint …, pause, resume"), ("?", "this help"), ("q", "quit"),
]


def help_screen(ctx: Ctx) -> View:
    lines = [[S(" Keys", "accent")], []]
    for k, v in HELP:
        lines.append([S(f" {k:<20}", "key"), S(v, "none")])
    lines.append([])
    lines.append([S(" What the words mean", "accent")])
    for k, v in (("rel_mse", "squared error divided by the error of 'repeat last season'; below 1.000 beats that reference"), ("stage", "s0 is a cheap first look, s1 and s2 are deeper tests; only deeper values are compared with each other"),
                 ("search / arm", "one running search strategy with its own record"), ("epoch", "the identity of the measuring code and data; results of different epochs are not comparable")):
        lines.append([S(f" {k:<20}", "key"), S(v, "dim")])
    lines.append([]); lines.append([S(" Read-only by construction: the ledger is opened read-only. `hint`, `pause` and `resume` are the only commands that write, and they go through the normal CLI.", "dim")])
    return View(lines)
