"""Pixel art for the terminal: half-block cells, a mint tile, a 4x5 pixel alphabet, bars and sparklines. Standard library only.

A picture is a list of rows of palette letters; two picture rows become one text row ('▀' with foreground = upper pixel, background = lower).
Styles are (foreground, background) names from PALETTE, so the same lines can be drawn by curses or written as ANSI.
"""
from __future__ import annotations

PALETTE = {"n": None, "i": "ink", "m": "mint", "d": "mint_dk", "p": "paper"}
SPARK = "▁▂▃▄▅▆▇█"

# THE TERMINAL ANT. The logo (assets/logo) stays as it is; in the terminal the ant is drawn the way a terminal draws things, in squares:
# 23 x 12 pixels facing right: abdomen, thorax with the cursor pixels ('d', they pulse: the heartbeat), square head, two antennae, three legs.
# 'i' = ink, 'd' = the cursor (mint), '.' = empty. The interface draws it with half blocks, two pixels per cell.
ANT_GRID = [
    "....................i..",
    "..................ii..i",
    ".................i..ii.",
    "..iiii...........i.i...",
    ".iiiiii..iiiiii.iiii...",
    "iiiiiiiiiiiddiiiiiii...",
    "iiiiiiiiiiiddiiiiiii...",
    ".iiiiii..iiiiii.iiii...",
    "..iiii....i.i.i........",
    ".........i..i..i.......",
    "........i...i...i......",
    "........i...i...i......",
]
GRID_W, GRID_H = 23, 12


def _tile_art(pad_x: int = 3, pad_y: int = 2) -> list[str]:
    """the mark centred on a mint tile (29 x 16 pixels = 29 cells x 8 rows)"""
    w, h = GRID_W + 2 * pad_x, GRID_H + 2 * pad_y
    g = [["m"] * w for _ in range(h)]
    for y, row in enumerate(ANT_GRID):
        for x, c in enumerate(row):
            if c in "id":
                g[y + pad_y][x + pad_x] = c
    return ["".join(r) for r in g]


_ANT_ART = _tile_art()

_FONT = {
    "a": [".##.", "...#", ".###", "#..#", ".###"],
    "n": ["###.", "#..#", "#..#", "#..#", "#..#"],
    "t": [".#..", "###.", ".#..", ".#..", ".##."],
    "s": [".###", "#...", ".##.", "...#", "###."],
}

Span = tuple  # (text, style) with style a name ("dim", "ok", ...) or a (fg, bg) pair of PALETTE names


def picture_lines(art: list[str]) -> list[list[Span]]:
    """two picture rows per text row; '▀' carries the upper pixel as foreground and the lower one as background"""
    rows = list(art) + ([""] if len(art) % 2 else [])
    out = []
    for y in range(0, len(rows), 2):
        up, lo = rows[y], rows[y + 1] if y + 1 < len(rows) else ""
        line: list[Span] = []
        for x in range(max(len(up), len(lo))):
            u = PALETTE.get(up[x] if x < len(up) else "n")
            l = PALETTE.get(lo[x] if x < len(lo) else "n")
            line.append(("▀" if (u or l) else " ", (u, l)))
        out.append(line)
    return out


def ant_lines(lit: bool = True) -> list[list[Span]]:
    """the ant on its tile; `lit` False dims the cursor pixels (call with pulse(t) to make it beat)"""
    art = _ANT_ART if lit else [r.replace("d", "p") for r in _ANT_ART]
    return picture_lines(art)


def word_lines(word: str = "ants", color: str = "ink", on: str | None = None) -> list[list[Span]]:
    """the word in a 4x5 alphabet, each pixel two cells wide so it is roughly square"""
    out = []
    for r in range(5):
        line: list[Span] = []
        for ch in word.lower():
            for c in _FONT.get(ch, ["....."] * 5)[r] + ".":
                line.append(("██" if c == "#" else "  ", (color, on) if c == "#" else (None, on)))
        out.append(line)
    return out


def side_by_side(left: list[list[Span]], right: list[list[Span]], gap: int = 3, valign: str = "middle") -> list[list[Span]]:
    h = max(len(left), len(right))
    lw = max((sum(len(t) for t, _ in l) for l in left), default=0)

    def pad(block, h, how):
        if len(block) >= h:
            return block
        extra = h - len(block)
        top = extra // 2 if how == "middle" else 0
        return [[] for _ in range(top)] + block + [[] for _ in range(extra - top)]
    L, R = pad(left, h, "top"), pad(right, h, valign)
    out = []
    for l, r in zip(L, R):
        w = sum(len(t) for t, _ in l)
        out.append(l + [(" " * (lw - w + gap), "none")] + r)
    return out


def bar(value: float, total: float, width: int = 20) -> str:
    """a bar in eighths of a cell: ████▌   (value clamped to 0..total)"""
    if total <= 0 or width <= 0:
        return " " * max(width, 0)
    eighths = int(round(max(0.0, min(1.0, value / total)) * width * 8))
    full, part = divmod(eighths, 8)
    return ("█" * full + (" ▏▎▍▌▋▊▉"[part] if part else "")).ljust(width)


def spark(values: list[float], width: int | None = None) -> str:
    """a sparkline of the values (the last `width` of them), scaled between their own min and max"""
    vals = [v for v in values if v is not None]
    if width:
        vals = vals[-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 8))] for v in vals)


# ---- charts -------------------------------------------------------------------------------------------------------------
SHADES = "·░▒▓█"
_BRAILLE_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))      # dot bits of the left and right column of a braille cell, top to bottom


def shade(v: float, vmax: float) -> str:
    """one cell of a heat strip: ·░▒▓█ by value"""
    if vmax <= 0 or v <= 0:
        return SHADES[0]
    return SHADES[min(4, 1 + int(v / vmax * 3.999))]


def braille_plot(values: list, width: int, height: int, lo: float | None = None, hi: float | None = None) -> list[str]:
    """a line chart in braille: `height` text rows of `width` cells, 2 x 4 dots per cell. Lower values are drawn lower; the line is
    connected between columns. Returns the rows top to bottom. Empty input gives blank rows."""
    vals = [float(v) for v in values if v is not None]
    if not vals or width < 1 or height < 1:
        return [" " * max(width, 0)] * max(height, 0)
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    span = (hi - lo) or 1.0
    cols, rows = width * 2, height * 4
    n = len(vals)
    ys = []
    for x in range(cols):
        v = vals[min(n - 1, int(x * n / cols))] if n < cols else vals[min(n - 1, round(x * (n - 1) / max(cols - 1, 1)))]
        ys.append(int(round((hi - v) / span * (rows - 1))))               # 0 = top row of dots
    cells = [[0] * width for _ in range(height)]

    def dot(x, y):
        cells[y // 4][x // 2] |= _BRAILLE_BITS[x % 2][y % 4]
    for x in range(cols):
        y0 = ys[x - 1] if x else ys[x]
        for y in range(min(y0, ys[x]), max(y0, ys[x]) + 1):
            dot(x, y)
    return ["".join(chr(0x2800 + c) for c in row) for row in cells]


def log_position(v: float, lo: float, hi: float, width: int) -> int:
    """column of a value on a log scale (for the parameter-count lollipop)"""
    import math
    if v <= 0 or lo <= 0 or hi <= lo or width < 2:
        return 0
    return max(0, min(width - 1, int(round((math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) * (width - 1)))))
