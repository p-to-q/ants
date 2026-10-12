#!/usr/bin/env python3
"""Regenerate the logo set in assets/logo/. Standard library only; run: python assets/make_logo.py

Every number below is part of the spec in assets/logo/README.md.
  unit   X = 14 design units = the side of the square head. Distances are multiples of X.
  mark   body box 56.3 x 39.3 units (antennae included). It is centred on its bounding box, never on an origin.
  tile   mark width = 60% of the tile, corner radius = 22.5% of the side, mark centred both ways.
  lockup gap between mark and name = 1X, name baseline on the bottom of the mark, name size = 1.2 x mark height.
  plate  lockup centred with 2X padding all round, corner radius = 22.5% of the plate height.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_figures as F  # noqa: E402  (palette, type and figure helpers)

OUT = HERE / "logo"
X = 14.0
BBOX = (7.0, 10.7, 63.3, 50.0)                     # of the mark drawn in its own 64-unit box, strokes included
MW, MH = BBOX[2] - BBOX[0], BBOX[3] - BBOX[1]      # 56.3 x 39.3
RADIUS = 0.225
TILE_MARK = 0.60
NAME_SIZE = 1.2 * MH
NAME_W = 1.60 * NAME_SIZE                          # "ants" is 1.60 em wide in the serif; textLength pins it for every fallback font
INK, PAPER, MINT_2, MINT_3, HAIR = F.INK, F.PAPER, F.MINT_2, F.MINT_3, F.HAIR


def shapes(ink=INK, accent=MINT_3):
    return F.mark_shapes(ink, accent)


def place(cx, cy, width, ink=INK, accent=MINT_3):
    """centre the mark's bounding box on (cx, cy) at the given width"""
    k = width / MW
    tx, ty = cx - k * (BBOX[0] + MW / 2), cy - k * (BBOX[1] + MH / 2)
    return f'<g transform="translate({tx:.3f},{ty:.3f}) scale({k:.4f})">{shapes(ink, accent)}</g>'


def svg(viewbox, width, body, label):
    x, y, w, h = viewbox
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:g} {y:g} {w:g} {h:g}" width="{width:g}" height="{width * h / w:g}" '
            f'role="img" aria-label="{label}">{body}</svg>\n')


def name_text(x, baseline, fill=INK):
    return (f'<text x="{x:.2f}" y="{baseline:.2f}" font-family="{F.SERIF}" font-size="{NAME_SIZE:.2f}" fill="{fill}" '
            f'textLength="{NAME_W:.2f}" lengthAdjust="spacingAndGlyphs">ants</text>')


def lockup(ox, oy, ink=INK, accent=MINT_3):
    """mark and name, top-left of the lockup at (ox, oy); returns (svg, width, height)"""
    g = place(ox + MW / 2, oy + MH / 2, MW, ink, accent) + name_text(ox + MW + X, oy + MH, ink)
    return g, MW + X + NAME_W, MH


def build():
    OUT.mkdir(exist_ok=True)
    # 1. mark, no text, transparent: clear space 1X all round
    (OUT / "mark.svg").write_text(svg((-X, -X, MW + 2 * X, MH + 2 * X), 4 * (MW + 2 * X), place(MW / 2, MH / 2, MW), "ants mark"))
    # 2. tile, no text: mark 60% of the tile, corner radius 22.5%
    S = 512
    for name, fill, ink, acc in (("tile", MINT_2, INK, MINT_3), ("tile-dark", INK, PAPER, MINT_3)):
        body = f'<rect width="{S}" height="{S}" rx="{S * RADIUS:.1f}" fill="{fill}"/>' + place(S / 2, S / 2, S * TILE_MARK, ink, acc)
        (OUT / f"{name}.svg").write_text(svg((0, 0, S, S), 256, body, "ants mark on a tile"))
    # 3. wordmark, mark and name, transparent: clear space 1X all round
    g, w, h = lockup(0, 0)
    (OUT / "wordmark.svg").write_text(svg((-X, -X, w + 2 * X, h + 2 * X), 3 * (w + 2 * X), g, "ants"))
    # 4. wordmark on a plate: lockup centred, padding 2X, corner radius 22.5% of the height
    pad = 2 * X
    pw, ph = w + 2 * pad, h + 2 * pad
    plate = f'<rect width="{pw:.2f}" height="{ph:.2f}" rx="{ph * RADIUS:.2f}" fill="{PAPER}" stroke="{HAIR}"/>'
    g, _, _ = lockup(pad, pad)
    (OUT / "wordmark-plate.svg").write_text(svg((0, 0, pw, ph), 3 * pw, plate + g, "ants"))
    construction(w, h)


def construction(lw, lh):
    """the spec drawn on the artwork: assets/logo/construction.svg"""
    TOP = F.TOP
    H = TOP + 304 + F.PAD
    o = F.head("Logo construction", "One unit is the side of the square head; every distance is a multiple of it.")
    # left: the tile with its guides
    S, x0, y0 = 300, F.PAD, TOP
    r = S * RADIUS
    o.append(f'<rect x="{x0}" y="{y0}" width="{S}" height="{S}" rx="{r:.1f}" fill="{MINT_2}"/>')
    cx, cy, mw = x0 + S / 2, y0 + S / 2, S * TILE_MARK
    o.append(f'<line x1="{x0}" x2="{x0 + S}" y1="{cy}" y2="{cy}" stroke="{INK}" stroke-opacity="0.25" stroke-dasharray="{F.DASH}"/>')
    o.append(f'<line x1="{cx}" x2="{cx}" y1="{y0}" y2="{y0 + S}" stroke="{INK}" stroke-opacity="0.25" stroke-dasharray="{F.DASH}"/>')
    k = mw / MW
    o.append(f'<rect x="{cx - mw / 2:.1f}" y="{cy - MH * k / 2:.1f}" width="{mw:.1f}" height="{MH * k:.1f}" fill="none" stroke="{INK}" stroke-dasharray="{F.DASH}"/>')
    o.append(place(cx, cy, mw))
    o.append(F.t(cx, cy + MH * k / 2 + 22, "mark width 60%", 11.5, INK, "middle", 500))
    o.append(f'<path d="M{x0},{y0 + r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x0 + r:.1f},{y0}" fill="none" stroke="{INK}" stroke-width="1.4"/>')
    o.append(f'<line x1="{x0 + r:.1f}" y1="{y0 + r:.1f}" x2="{x0 + r - r * 0.7071:.1f}" y2="{y0 + r - r * 0.7071:.1f}" stroke="{INK}" stroke-width="1"/>')
    o.append(f'<circle cx="{x0 + r:.1f}" cy="{y0 + r:.1f}" r="2.5" fill="{INK}"/>')
    o.append(F.t(x0 + r + 16, y0 + 24, "corner radius 22.5%", 11.5, INK, "start", 500))
    # right: the lockup with clear space, gap and baseline
    s = 2.8
    ox, oy = 372, TOP + (S - (lh + 2 * X) * s) / 2
    o.append(f'<rect x="{ox}" y="{oy:.1f}" width="{(lw + 2 * X) * s:.1f}" height="{(lh + 2 * X) * s:.1f}" rx="12" fill="none" stroke="{MINT_3}" stroke-dasharray="{F.DASH}"/>')
    o.append(f'<g transform="translate({ox + X * s:.1f},{oy + X * s:.1f}) scale({s})">' + lockup(0, 0)[0] + "</g>")
    by = oy + (X + lh) * s
    o.append(f'<line x1="{ox}" x2="{ox + (lw + 2 * X) * s:.1f}" y1="{by:.1f}" y2="{by:.1f}" stroke="{INK}" stroke-opacity="0.3"/>')
    o.append(F.t(ox + 8, oy + 18, "1X clear space", 11.5, INK, "start", 500))
    gx = ox + (X + MW) * s
    o.append(f'<rect x="{gx:.1f}" y="{oy + (X + lh) * s + 8:.1f}" width="{X * s:.1f}" height="8" fill="none" stroke="{INK}"/>')
    o.append(F.t(gx + X * s / 2, oy + (X + lh) * s + 34, "1X", 11.5, INK, "middle", 500))
    o.append(F.t(ox + (lw + 2 * X) * s - 8, by + 26, "baseline = bottom of the mark", 11.5, INK, "end", 500))
    (OUT / "construction.svg").write_text(F.canvas(H, o, "Logo construction"))


MUTE_TXT = F.MUTE

if __name__ == "__main__":
    build()
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))
