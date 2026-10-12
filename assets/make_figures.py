#!/usr/bin/env python3
"""Regenerate assets/*.svg (banner, architecture, cascade, roles, ladder, chain, scatter, frontier, benchmark). Standard library only.

    python assets/make_figures.py

Design rules (keep them when adding a figure)
  grid    900 px wide, 32 px margins, 8 px rhythm. Title baseline 58, subtitle 86, content from 124, 32 px below the content.
  type    Inter. Titles and small headings semibold, labels medium, body regular; tabular numerals. No italics, no serif.
  colour  paper ground, near-black ink, three greys, one mint ramp (light to deep) that always means "ours / further along".
  shape   radius 12 on cards and cells, 8 on bars, pills for chips, one dash pattern (3 3), 1 px lines, one arrowhead.
  words   titles are nouns; one short factual subtitle under the title; one sentence per card; no footers.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "assets"

PAPER, CARD = "#f5f5f3", "#fcfcfb"
INK, MUTE, HAIR, FILL, GHOST = "#1b1d1b", "#7d827c", "#e2e4df", "#bcc3be", "#a3aaa5"
MINT_1, MINT_2, MINT_3, MINT_TEXT = "#e1f5ee", "#a8e6d7", "#5ccfb1", "#17917a"
SANS = ("Inter,'Inter Variable',ui-sans-serif,-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif")
NUM = "font-variant-numeric:tabular-nums;font-feature-settings:'tnum'"
SERIF = "'Martina Plantijn',martina,Plantin,Crimson,'Crimson Text','Source Serif 4',Georgia,'Times New Roman',serif"   # wordmark only
DASH = "3 3"
W, PAD, TOP = 900, 32, 124


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def wt(w):
    """semibold titles and small headings, medium labels, regular body"""
    return 700 if w >= 700 else 600 if w >= 600 else 500 if w >= 500 else 400 if w >= 400 else 300


def t(x, y, s, size=13, fill=INK, anchor="start", weight=400, track=0):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{SANS}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{wt(weight)}" letter-spacing="{track}" style="{NUM}">{esc(s)}</text>')


def t2(x, y, parts, size=12, anchor="start"):
    """one text element, several runs: [(text, fill, weight)]"""
    runs = "".join(f'<tspan fill="{f}" font-weight="{wt(w)}">{esc(s)}</tspan>' for s, f, w in parts)
    return f'<text x="{x:.1f}" y="{y:.1f}" font-family="{SANS}" font-size="{size}" text-anchor="{anchor}" style="{NUM}">{runs}</text>'


LABELS = []        # text boxes of the figure being drawn: (x0, y0, x1, y1, text); checked for overlaps before the file is written


def box(x, y, s, size, anchor, weight=400):
    w = len(s) * size * (0.60 if wt(weight) >= 600 else 0.56)
    x0 = x if anchor == "start" else x - w if anchor == "end" else x - w / 2
    return (x0, y - size * 0.8, x0 + w, y + size * 0.25)


def label(o, x, y, s, size=12, fill=INK, anchor="start", weight=400, halo=True):
    """text with a paper-coloured halo, so it stays legible over lines; its box joins the collision check"""
    LABELS.append(box(x, y, s, size, anchor, weight) + (s,))
    if halo:
        o.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{SANS}" font-size="{size}" text-anchor="{anchor}" font-weight="{wt(weight)}" '
                 f'fill="none" stroke="{PAPER}" stroke-width="4" stroke-linejoin="round" style="{NUM}">{esc(s)}</text>')
    o.append(t(x, y, s, size, fill, anchor, weight))


def label2(o, x, y, parts, size=12, anchor="start"):
    """label with several runs, [(text, fill, weight)]"""
    full = "".join(p[0] for p in parts)
    LABELS.append(box(x, y, full, size, anchor, 500) + (full,))
    o.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{SANS}" font-size="{size}" text-anchor="{anchor}" fill="none" stroke="{PAPER}" '
             f'stroke-width="4" stroke-linejoin="round" style="{NUM}">{esc(full)}</text>')
    o.append(t2(x, y, parts, size, anchor))


def check_labels(name):
    bad = [(a[4], b[4]) for i, a in enumerate(LABELS) for b in LABELS[i + 1:]
           if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]]
    LABELS.clear()
    if bad:
        raise SystemExit(f"{name}: overlapping labels {bad}")


def head(title, sub):
    return [t(PAD, 58, title, 28, INK, weight=600, track=-0.7), t(PAD, 86, sub, 13, MUTE)]


def canvas(h, body, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" role="img" aria-label="{esc(label)}">'
            f'<rect width="{W}" height="{h}" rx="16" fill="{PAPER}"/>' + "".join(body) + "</svg>")


def card(x, y, w, h, fill=CARD, stroke=HAIR):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'


def arrow(x0, x1, y, color=INK):
    """one arrow style everywhere: a node at the source, a hairline, an open chevron at the target (either direction)"""
    d = 1 if x1 >= x0 else -1
    return (f'<circle cx="{x0}" cy="{y}" r="2.5" fill="{color}"/>'
            f'<line x1="{x0}" x2="{x1 - d}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="1"/>'
            f'<path d="M{x1 - d * 5},{y - 4} L{x1},{y} L{x1 - d * 5},{y + 4}" fill="none" stroke="{color}" stroke-width="1.2" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def pill(x, y, w, s, fill=CARD, stroke=HAIR, ink=INK, h=24, size=11.5):
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="{h/2}" fill="{fill}" stroke="{stroke}"/>'
            + t(x + w / 2, y + h / 2 + size * 0.36, s, size, ink, "middle", 500))


def curve(x0, y0, x1, y1, stroke=INK, w=1.0, op=1.0):
    mx = (x0 + x1) / 2
    return (f'<path d="M{x0:.1f},{y0:.1f} C{mx:.1f},{y0:.1f} {mx:.1f},{y1:.1f} {x1:.1f},{y1:.1f}" fill="none" '
            f'stroke="{stroke}" stroke-width="{w}" stroke-opacity="{op}"/>')


def mark(x, y, size, ink=INK, accent=MINT_3):
    """the ants mark in a 64-unit box: abdomen, thorax and square head touch edge to edge; the head is the one fixed thing"""
    k = size / 64
    # abdomen r=11 ends at x=29; the thorax (r=7.5 + 1.3 stroke) starts there and ends at 46.6, where the head begins
    g = [f'<circle cx="18" cy="39" r="11" fill="{ink}"/>',
         f'<circle cx="37.8" cy="39" r="7.5" fill="{accent}" stroke="{ink}" stroke-width="2.6"/>',
         f'<rect x="46.6" y="32" width="14" height="14" fill="{ink}"/>',
         f'<path d="M51,32 C51,21 55,15 60,12" fill="none" stroke="{ink}" stroke-width="2.6" stroke-linecap="round"/>',
         f'<path d="M57.5,32 C58.5,28 60,25 62,22" fill="none" stroke="{ink}" stroke-width="2.6" stroke-linecap="round"/>']
    return f'<g transform="translate({x},{y}) scale({k:.4f})">' + "".join(g) + "</g>"


def logo():
    """assets/logo.svg: the mark on a mint tile; assets/logo-wordmark.svg: mark and the name in the serif"""
    tile = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" width="256" height="256" role="img" aria-label="ants">'
            f'<rect width="96" height="96" rx="22" fill="{MINT_2}"/>' + mark(16, 15, 64) + "</svg>")
    (A / "logo.svg").write_text(tile)
    word = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 120" width="360" height="120" role="img" aria-label="ants">'
            f'<rect width="360" height="120" rx="16" fill="{PAPER}"/><rect x="20" y="20" width="80" height="80" rx="20" fill="{MINT_2}"/>'
            + mark(28, 23, 64)
            + f'<text x="122" y="76" font-family="{SERIF}" font-size="52" fill="{INK}" letter-spacing="-1">ants</text></svg>')
    (A / "logo-wordmark.svg").write_text(word)


# ---------------------------------------------------------------------------------------------------------------- banner
# layout options for the banner; README uses BANNER. Sizes are in banner units (the README shows it about 25% smaller).
BANNERS = {
    "a": dict(w=1200, h=380, hs=68, y1=190, y2=264, ts=20, ty=320, gx=800, cy=220, span=1.0),     # headline 68
    "b": dict(w=1200, h=460, hs=96, y1=222, y2=318, ts=24, ty=388, gx=830, cy=266, span=1.3),     # larger headline
    "c": dict(w=1200, h=320, hs=52, y1=160, y2=218, ts=17, ty=268, gx=800, cy=198, span=0.8),     # smaller, more air
    "d": dict(w=1200, h=272, hs=56, y1=0, y2=166, ts=18, ty=214, gx=830, cy=170, span=0.6),       # one line
}
BANNER = "a"


def banner_svg(cfg, weight=None):
    import base64
    logo = base64.b64encode((A / "p-to-q_logo.png").read_bytes()).decode()
    BW, BH = cfg["w"], cfg["h"]
    o = [f'<rect width="{BW}" height="{BH}" rx="16" fill="{MINT_2}"/>',
         f'<path d="M0,16 a16,16 0 0 1 16,-16 H{BW-16} a16,16 0 0 1 16,16 V72 H0 Z" fill="{PAPER}"/>',
         f'<line x1="0" x2="{BW}" y1="72" y2="72" stroke="{INK}" stroke-width="1"/>',
         mark(36, 17, 38),
         f'<text x="82" y="46" font-family="{SERIF}" font-size="28" fill="{INK}" letter-spacing="-0.4">ants</text>',
         f'<image href="data:image/png;base64,{logo}" x="{BW-40-93}" y="21" width="93" height="30"/>']
    hs, tr = cfg["hs"], -cfg["hs"] * 0.02

    def head_line(y, text):
        return (f'<text x="40" y="{y}" font-family="{SERIF}" font-size="{hs}" font-weight="{weight or 500}" fill="{INK}" '
                f'letter-spacing="{tr:.2f}">{esc(text)}</text>')
    o += [head_line(cfg["y1"], "Agents never"), head_line(cfg["y2"], "touch scoring.")] if cfg["y1"] else [head_line(cfg["y2"], "Agents never touch scoring.")]
    o.append(t(42, cfg["ty"], "A CPU-only research loop with an evidence ledger.", cfg["ts"], INK, weight=400))
    gx, cy, k = cfg["gx"], cfg["cy"], cfg["span"]
    for i, dy in enumerate((-70, -24, 22, 68)):
        y = cy + dy * k
        o.append(curve(gx, y, gx + 290, cy, INK if i % 2 == 0 else "#ffffff", 1.0, 0.85))
        o.append(f'<circle cx="{gx}" cy="{y:.1f}" r="4.5" fill="{INK}"/>')
    for dy in (-98, -42, 42, 98):
        y = cy + dy * k
        o.append(curve(gx - 52, y, gx + 150, cy, "#ffffff", 1.0, 0.9))
        o.append(f'<rect x="{gx-57}" y="{y-4.5:.1f}" width="9" height="9" fill="{MINT_3}"/>')
    o.append(f'<line x1="{gx+220}" x2="{gx+220}" y1="{cy-120*k:.1f}" y2="{cy+124*k:.1f}" stroke="{INK}" stroke-width="1" stroke-dasharray="{DASH}"/>')
    o.append(f'<rect x="{gx+284}" y="{cy-6}" width="12" height="12" fill="{INK}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BW} {BH}" width="{BW}" role="img" '
            f'aria-label="ants: Agents never touch scoring">' + "".join(o) + "</svg>")


BANNER_WEIGHT = 500


def banner():
    (A / "banner.svg").write_text(banner_svg(BANNERS[BANNER], BANNER_WEIGHT))


# ---------------------------------------------------------------------------------------------------------- architecture
def architecture():
    ch, led = 132, 56
    h = TOP + ch + 28 + led + PAD
    o = head("Propose, judge, execute", "Proposers can only hand specs to the judge.")
    cw, gap = 228, 76
    cols = [("Propose", ["Suggests specs and is", "trusted with nothing."], CARD, HAIR),
            ("Judge", ["Validates, prices, evaluates", "and records every spec."], MINT_2, MINT_2),
            ("Execute", ["Runs each trial in its own", "sandboxed process."], CARD, HAIR)]
    for i, (name, lines, fill, stroke) in enumerate(cols):
        x = PAD + i * (cw + gap)
        o.append(card(x, TOP, cw, ch, fill, stroke))
        o.append(t(x + 24, TOP + 44, name, 17, INK, weight=600, track=-0.3))
        for j, s_ in enumerate(lines):
            o.append(t(x + 24, TOP + 76 + j * 21, s_, 13, INK if fill != CARD else "#4d524d"))
    for i in (0, 1):                                          # forward: the spec goes right
        g = PAD + (i + 1) * cw + i * gap
        o.append(arrow(g + 10, g + gap - 10, TOP + 50))
        o.append(t(g + gap / 2, TOP + 40, "spec", 11.5, MUTE, "middle", 500))
    g = PAD + 2 * cw + gap                                    # back: the result comes left
    o.append(arrow(g + gap - 10, g + 10, TOP + 94))
    o.append(t(g + gap / 2, TOP + 114, "result", 11.5, MUTE, "middle", 500))
    wall = PAD + cw + gap / 2
    o.append(f'<line x1="{wall}" x2="{wall}" y1="{TOP-14}" y2="{TOP+ch+14}" stroke="{INK}" stroke-width="1" stroke-dasharray="{DASH}"/>')
    ly = TOP + ch + 28                                        # the ledger: one trail under all three
    for i in range(3):
        cx = PAD + i * (cw + gap) + cw / 2
        o.append(f'<line x1="{cx}" x2="{cx}" y1="{TOP+ch}" y2="{ly - 3}" stroke="{INK}" stroke-width="1"/>')
        o.append(f'<circle cx="{cx}" cy="{TOP+ch}" r="2.5" fill="{INK}"/>')
    o.append(card(PAD, ly, W - 2 * PAD, led, CARD, HAIR))
    o.append(t(PAD + 24, ly + 34, "Ledger", 15, INK, weight=600, track=-0.2))
    o.append(t(PAD + 96, ly + 34, "Hash-chained and fenced by epochs; every spec, result and verdict is recorded.", 13, "#4d524d"))
    (A / "architecture.svg").write_text(canvas(h, o, "Propose, judge, execute"))


# ------------------------------------------------------------------------------------------------------------------ cascade
def cascade():
    ch = 240
    h = TOP + ch + PAD
    o = head("Cascade", "Each stage keeps the best quarter, so 16 candidates cost about 53 s instead of 256 s.")
    cw, gap = 244, 52
    stages = [("S0", "1 dataset, 1 fold", "0.2–0.4 s", "about 5 s in total", 16, None),
              ("S1", "3 datasets, 3 folds", "4–12 s", "about 32 s in total", 4, "25% of S0"),
              ("S2", "4 datasets, 3 folds", "9–24 s", "about 16 s in total", 1, "25% of S1")]
    for i, (name, sub, cost, total, keep, tag) in enumerate(stages):
        x = PAD + i * (cw + gap)
        o.append(card(x, TOP, cw, ch))
        o.append(t(x + 24, TOP + 44, name, 17, INK, weight=600, track=-0.3))
        o.append(t(x + 24, TOP + 66, sub, 12, MUTE))
        if tag:
            o.append(t(x + cw - 24, TOP + 44, tag, 11.5, MINT_TEXT, "end", 600))
        for k in range(16):
            cx, cy = x + 30 + (k % 8) * 26, TOP + 104 + (k // 8) * 26
            on = k < keep
            o.append(f'<circle cx="{cx}" cy="{cy}" r="6" fill="{MINT_3 if on else FILL}" fill-opacity="{1 if on else 0.5}"/>')
        o.append(f'<line x1="{x+24}" x2="{x+cw-24}" y1="{TOP+156}" y2="{TOP+156}" stroke="{HAIR}"/>')
        o.append(t(x + 24, TOP + 194, cost, 22, INK, weight=600, track=-0.5))
        o.append(t(x + 24, TOP + 216, "per trial · " + total, 12, MUTE))
        if i < 2:
            o.append(arrow(x + cw + 8, x + cw + gap - 8, TOP + 117))
    (A / "cascade.svg").write_text(canvas(h, o, "Cascade of three stages"))


# --------------------------------------------------------------------------------------------------------------------- roles
def roles():
    rows = [("dev", "validation windows", 0, "many looks", MINT_2, INK),
            ("confirm", "never seen by the search", 1, "few looks", MINT_1, INK),
            ("sealed", "whole source families", 2, "one look", INK, PAPER),
            ("stress", "synthetic, diagnostic", 3, "not in the headline", CARD, INK)]
    cols = ["Search", "Confirmation", "Claim", "Diagnostics"]
    lw, cell_w, gap, rh = 232, 144, 8, 56
    x0 = PAD + lw
    h = TOP + 28 + len(rows) * (rh + gap) - gap + PAD
    o = head("Data roles", "Each dataset has one role, set in the catalog and never by an agent.")
    for j, name in enumerate(cols):
        o.append(t(x0 + j * (cell_w + gap) + cell_w / 2, TOP + 8, name, 12, MUTE, "middle", 500))
    for i, (name, desc, col, label, fill, ink) in enumerate(rows):
        y = TOP + 28 + i * (rh + gap)
        o.append(t(PAD, y + 24, name, 15, INK, weight=600))
        o.append(t(PAD, y + 43, desc, 12, MUTE))
        for j in range(4):
            cx = x0 + j * (cell_w + gap)
            if j == col:
                o.append(card(cx, y, cell_w, rh, fill, HAIR if fill == CARD else fill))
                o.append(t(cx + cell_w / 2, y + rh / 2 + 4.5, label, 13, ink, "middle", 500))
            else:
                o.append(f'<circle cx="{cx + cell_w / 2}" cy="{y + rh / 2}" r="2.5" fill="{FILL}"/>')
    (A / "roles.svg").write_text(canvas(h, o, "Dataset roles"))


# -------------------------------------------------------------------------------------------------------------------- ladder
def ladder():
    bw, gap = 190, 25
    heights = [96, 128, 160, 192]
    base = TOP + heights[-1]
    track = base + 32
    h = track + 56 + PAD - 8
    o = head("Promotion ladder", "A model moves up one rung at a time, and only by passing the gate between them.")
    rungs = [("candidate", "cleared S2", CARD, HAIR), ("confirmed", "paired test", MINT_1, HAIR),
             ("validated", "many families", MINT_2, MINT_2), ("champion", "one sealed look", MINT_3, MINT_3)]
    gates = ["confirm data", "families", "one look"]
    o.append(f'<line x1="{PAD}" x2="{W-PAD}" y1="{track}" y2="{track}" stroke="{INK}" stroke-width="1"/>')
    for i, (name, sub, fill, stroke) in enumerate(rungs):
        x, hh = PAD + i * (bw + gap), heights[i]
        o.append(card(x, base - hh, bw, hh, fill, stroke))
        o.append(t(x + 22, base - hh + 38, name, 16, INK, weight=600, track=-0.3))
        o.append(t(x + 22, base - hh + 60, sub, 12.5, INK if i >= 2 else MUTE))
        if i < 3:
            gx = x + bw + gap / 2
            o.append(f'<line x1="{gx}" x2="{gx}" y1="{base - 24}" y2="{track}" stroke="{INK}" stroke-width="1"/>')
            o.append(f'<circle cx="{gx}" cy="{track}" r="6" fill="{PAPER}" stroke="{INK}" stroke-width="1.5"/>')
            o.append(t(gx, track + 30, gates[i], 12, INK, "middle", 500))
    (A / "ladder.svg").write_text(canvas(h, o, "Promotion ladder"))


# -------------------------------------------------------------------------------------------------------------------- chain
def chain():
    n, bw, gap = 5, 140, 34
    ch = 88
    h = TOP + ch + 24 + 28 + PAD
    o = head("Ledger", "Each event carries the hash of the event before it.")
    for i in range(n):
        x, last = PAD + i * (bw + gap), i == n - 1
        o.append(card(x, TOP, bw, ch, MINT_2 if last else CARD, MINT_2 if last else HAIR))
        o.append(t(x + 18, TOP + 32, "head" if last else f"event {i + 1}", 12, INK if last else MUTE, weight=500))
        o.append(t(x + 18, TOP + 58, "H(0, body)" if i == 0 else "H(prev, body)", 13, INK, weight=600))
        o.append(f'<rect x="{x+18}" y="{TOP+70}" width="{bw-36}" height="4" rx="2" fill="{MINT_3 if last else FILL}"/>')
        if i < n - 1:
            o.append(arrow(x + bw + 8, x + bw + gap - 8, TOP + 44))
    hx = PAD + (n - 1) * (bw + gap) + bw / 2
    o.append(f'<line x1="{hx}" x2="{hx}" y1="{TOP+ch}" y2="{TOP+ch+24}" stroke="{INK}" stroke-width="1" stroke-dasharray="{DASH}"/>')
    o.append(pill(hx - 74, TOP + ch + 24, 148, "anchor off the box", PAPER, INK, INK, 28, 12))
    (A / "chain.svg").write_text(canvas(h, o, "Hash-chained ledger"))


# --------------------------------------------------------------------------------------------------------------------- bars
def bar(cx, base, w, h, kind):
    x, y = cx - w / 2, base - h
    if kind == "ghost":
        return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="8" fill="none" stroke="{GHOST}" stroke-dasharray="{DASH}"/>'
    if kind == "ghost_ours":
        return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="8" fill="{MINT_1}" stroke="{MINT_3}" stroke-dasharray="{DASH}"/>'
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="8" fill="{MINT_2 if kind == "ours" else FILL}"/>'


def panel_bars(o, x, w, base, ph, vmax, items, title, sub):
    """one dots-style panel: title, grey subtitle, rounded bars with the value above and name and size below"""
    o.append(t(x, TOP + 14, title, 14, INK, weight=600))
    o.append(t(x, TOP + 34, sub, 12, MUTE))
    pitch = w / len(items)
    for i, (name, params, val, kind) in enumerate(items):
        cx = x + pitch * (i + 0.5)
        if val is None:
            o.append(bar(cx, base, 44, ph * 0.5, kind))
            o.append(t(cx, base - ph * 0.5 - 12, "pending", 12, MUTE, "middle", 500))
        else:
            hh = ph * val / vmax
            o.append(bar(cx, base, 44, hh, kind))
            o.append(t(cx, base - hh - 12, f"{val:.1f}", 12.5, MINT_TEXT if kind == "ours" else "#454a46", "middle", 600 if kind == "ours" else 500))
        o.append(t(cx, base + 26, name, 12, INK, "middle", 500 if kind != "other" else 400))
        o.append(t(cx, base + 44, "" if params is None else f"{params:,}", 11.5, MUTE, "middle"))
    o.append(f'<line x1="{x}" x2="{x + w}" y1="{base}" y2="{base}" stroke="{HAIR}"/>')


def frontier():
    d = json.loads((A / "frontier.json").read_text())
    names = {"ridge_L720": "ridge 720", "ridge_L336": "ridge 336", "ridge_L96": "ridge 96", "dlinear_like": "DLinear-like", "fits_like": "FITS-like"}
    ours = [("ants ≤ 100", None, None, "ghost_ours")]
    ours += [(f"≤ {int(b['cap'])}", b["params"], 100 * (1 - b["rel_mse"]), "ours") for b in d["ants_demo"]["best_by_bin"]]
    ours[0] = ("≤ 100", None, None, "ghost_ours")
    base_items = sorted([(names[k], v["params"], 100 * (1 - v["rel_mse"]), "other") for k, v in d["measured"].items()], key=lambda r: r[1])
    ph = 200
    base = TOP + 64 + ph
    h = base + 72 + PAD - 24
    o = head("Skill versus parameters", "Percent MSE reduction against seasonal naive on development data, at stage S2.")
    lw, gap = 372, 56
    panel_bars(o, PAD, lw, base, ph, 24.0, ours, f"ants, {d['ants_demo']['finished_trials']} trials", "best model under each parameter cap")
    panel_bars(o, PAD + lw + gap, W - 2 * PAD - lw - gap, base, ph, 24.0, base_items, "Hand-built baselines", "fixed specs, not tuned")
    (A / "frontier.svg").write_text(canvas(h, o, "Skill versus parameters"))


def scatter():
    d = json.loads((A / "frontier.json").read_text())
    H, L, R, T, B = 520, 80, 48, 160, 76
    x0, x1, y0, y1 = math.log10(100), math.log10(1e6), 0.76, 1.03
    X = lambda p: L + (math.log10(p) - x0) / (x1 - x0) * (W - L - R)
    Y = lambda v: T + (y1 - v) / (y1 - y0) * (H - T - B)
    o = head("Parameters versus error", "rel_mse against parameters on development data at stage S2; lower is better.")
    # the zone ants works in: at or under 3 000 parameters (the first design's shaded region, kept)
    o.append(f'<rect x="{X(100):.1f}" y="{T-12}" width="{X(3000)-X(100):.1f}" height="{H-B-T+12}" rx="12" fill="{MINT_2}" fill-opacity="0.34"/>')
    for c, lab in ((100, "100"), (300, "300"), (1000, "1k"), (3000, "3k")):
        o.append(f'<line x1="{X(c):.1f}" x2="{X(c):.1f}" y1="{T}" y2="{H-B}" stroke="{MINT_3}" stroke-opacity="0.6" stroke-dasharray="{DASH}"/>')
        label(o, X(c), T - 22, lab, 11.5, MINT_TEXT, "middle", 600, halo=False)
    label(o, X(100) + 14, T + 6, "parameter caps", 11.5, MINT_TEXT, "start", 600, halo=False)
    # references
    for v in (0.8, 0.9):
        o.append(f'<line x1="{L}" x2="{W-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{HAIR}"/>')
        label(o, L - 14, Y(v) + 4, f"{v:.2f}", 11.5, MUTE, "end", halo=False)
    o.append(f'<line x1="{L}" x2="{W-R}" y1="{Y(1.0):.1f}" y2="{Y(1.0):.1f}" stroke="{INK}" stroke-dasharray="{DASH}"/>')
    label(o, L - 14, Y(1.0) + 4, "1.00", 11.5, MUTE, "end", halo=False)
    label(o, W - R, Y(1.0) - 10, "seasonal naive", 12, MUTE, "end", 500, halo=False)
    for p, lab in ((100, "100"), (1e3, "1k"), (1e4, "10k"), (1e5, "100k"), (1e6, "1M")):
        o.append(f'<line x1="{X(p):.1f}" x2="{X(p):.1f}" y1="{H-B}" y2="{H-B+6}" stroke="{FILL}"/>')
        label(o, X(p), H - B + 26, lab, 11.5, MUTE, "middle", halo=False)
    o.append(f'<line x1="{L}" x2="{W-R}" y1="{H-B}" y2="{H-B}" stroke="{FILL}"/>')
    label(o, W - R, H - 22, "parameters, log scale", 11.5, MUTE, "end", 500, halo=False)
    # campaign results that do not exist yet: hollow markers on the floor of the plot, as in the first design
    for c in (100, 300, 1000, 3000):
        o.append(f'<circle cx="{X(c):.1f}" cy="{Y(0.775):.1f}" r="5" fill="{PAPER}" stroke="{INK}" stroke-dasharray="{DASH}"/>')
    label(o, X(3000) + 16, Y(0.775) + 4, "campaign: pending", 11.5, MUTE, "start", 500, halo=False)
    # hand-built baselines and their best-so-far envelope
    names = {"ridge_L720": "ridge 720", "ridge_L336": "ridge 336", "ridge_L96": "ridge 96", "dlinear_like": "DLinear-like", "fits_like": "FITS-like"}
    pos = {"ridge_L720": ("end", -13, 5), "ridge_L336": ("end", -13, 5), "dlinear_like": ("end", -13, 5), "ridge_L96": ("middle", 0, -14), "fits_like": ("middle", 0, -14)}
    base = sorted(((v["params"], v["rel_mse"], k) for k, v in d["measured"].items()))
    env, best = [], 9
    for p, v, k in base:
        if v < best:
            best = v; env.append((p, v))
    path = f"M{X(env[0][0]):.1f},{Y(env[0][1]):.1f}"
    for (p0, v0), (p1, v1) in zip(env, env[1:]):
        path += f" L{X(p1):.1f},{Y(v0):.1f} L{X(p1):.1f},{Y(v1):.1f}"
    o.append(f'<path d="{path}" fill="none" stroke="{INK}" stroke-opacity="0.3" stroke-width="1.2"/>')
    for p, v, k in base:
        cx, cy = X(p), Y(v)
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{INK}"/>')
        an, dx, dy = pos[k]
        label2(o, cx + dx, cy + dy, [(names[k] + "  ", MUTE, 400), (f"{v:.2f}", INK, 600)], 12, an)
    # ants: best model under each cap as a step line that ends at the cap
    pts, best = [], 9
    for b in d["ants_demo"]["best_by_bin"]:
        if b["rel_mse"] < best:
            best = b["rel_mse"]; pts.append((b["params"], b["rel_mse"], b["cap"]))
    path = f"M{X(pts[0][0]):.1f},{Y(pts[0][1]):.1f}"
    for (p0, v0, _), (p1, v1, _) in zip(pts, pts[1:]):
        path += f" L{X(p1):.1f},{Y(v0):.1f} L{X(p1):.1f},{Y(v1):.1f}"
    path += f" L{X(pts[-1][2]):.1f},{Y(pts[-1][1]):.1f}"
    o.append(f'<path d="{path}" fill="none" stroke="{MINT_3}" stroke-width="2.4" stroke-linejoin="round"/>')
    pa, va, _ = pts[-1]
    fp, fv = d["measured"]["fits_like"]["params"], d["measured"]["fits_like"]["rel_mse"]
    o.append(f'<line x1="{X(pa):.1f}" x2="{X(fp):.1f}" y1="{Y(va)+0.5:.1f}" y2="{Y(va)+0.5:.1f}" stroke="{INK}" stroke-width="1" stroke-dasharray="{DASH}"/>')
    label(o, (X(3000) + X(fp)) / 2, Y(va) + 24, f"{fp / pa:.1f}× fewer parameters", 12, INK, "middle", 500)
    lab = [("end", -13, -14), ("start", 12, -14), ("end", -13, 22)]
    for i, (p, v, cap) in enumerate(pts):
        o.append(f'<rect x="{X(p)-6:.1f}" y="{Y(v)-6:.1f}" width="12" height="12" rx="2.5" fill="{MINT_3}" stroke="{INK}"/>')
        an, dx, dy = lab[min(i, 2)]
        label(o, X(p) + dx, Y(v) + dy, f"{v:.2f}", 12, MINT_TEXT, an, 600)
    label(o, X(pts[0][0]) - 13, Y(pts[0][1]) + 6, "ants", 12.5, INK, "end", 600)
    check_labels("scatter")
    (A / "scatter.svg").write_text(canvas(H, o, "Parameters versus error"))


def benchmark():
    caps = ["100", "300", "1 000", "3 000"]
    arms = [("ants", "ghost_ours"), ("random 2×", "ghost"), ("TPE", "ghost"), ("evolution", "ghost"), ("FITS-like", "ghost"), ("ridge", "ghost")]
    pw, gap, ph = 190, 25, 128
    base = TOP + 40 + ph
    h = base + 36 + 44 + PAD - 8
    o = head("TinyTS-Bench", "Pre-registered: every bar stays empty until a campaign has run.")
    for i, cap in enumerate(caps):
        x = PAD + i * (pw + gap)
        o.append(t(x, TOP + 14, f"Cap {cap}", 14, INK, weight=600))
        o.append(t(x, TOP + 34, "rel_mse, 6 seeds", 12, MUTE))
        for j, (_, kind) in enumerate(arms):
            o.append(bar(x + 14 + j * 30, base, 20, ph * (0.7 if j == 0 else 0.6), kind))
        o.append(f'<line x1="{x}" x2="{x+pw}" y1="{base}" y2="{base}" stroke="{HAIR}"/>')
    ly = base + 36
    o.append(f'<rect x="{PAD}" y="{ly}" width="{W-2*PAD}" height="44" rx="12" fill="none" stroke="{HAIR}"/>')
    lx = PAD + 24
    for name, kind in arms:
        o.append(f'<rect x="{lx}" y="{ly+16}" width="12" height="12" rx="4" fill="{MINT_1 if kind == "ghost_ours" else "none"}" '
                 f'stroke="{MINT_3 if kind == "ghost_ours" else GHOST}" stroke-dasharray="{DASH}"/>')
        o.append(t(lx + 20, ly + 27, name, 12, INK, weight=500 if kind == "ghost_ours" else 400))
        lx += 20 + 7 * len(name) + 36
    (A / "benchmark.svg").write_text(canvas(h, o, "TinyTS-Bench, pending"))


if __name__ == "__main__":
    logo(); banner(); architecture(); cascade(); roles(); ladder(); chain(); scatter(); frontier(); benchmark()
    print("wrote figures in", A)
