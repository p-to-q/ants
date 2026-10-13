"""Pixel art for the terminal: half-block cells, a mint tile, a 4x5 pixel alphabet, bars and sparklines. Standard library only.

A picture is a list of rows of palette letters; two picture rows become one text row ('▀' with foreground = upper pixel, background = lower).
Styles are (foreground, background) names from PALETTE, so the same lines can be drawn by curses or written as ANSI.
"""
from __future__ import annotations

PALETTE = {"n": None, "i": "ink", "m": "mint", "d": "mint_dk", "p": "paper"}
SPARK = "▁▂▃▄▅▆▇█"

# the mark: abdomen, thorax ring, square head, two antennae; drawn on a mint tile (30 x 16 pixels), built from shapes so the proportions stay exact
def _ant_art(w: int = 30, h: int = 16) -> list[str]:
    g = [["m"] * w for _ in range(h)]

    def disc(cx, cy, rx, ry, ch):
        for y in range(h):
            for x in range(w):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0:
                    g[y][x] = ch

    def line(x0, y0, x1, y1, ch="i"):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for k in range(n + 1):
            g[round(y0 + (y1 - y0) * k / n)][round(x0 + (x1 - x0) * k / n)] = ch
    disc(8.0, 9.0, 6.3, 5.2, "i")                 # abdomen
    disc(17.5, 9.0, 3.9, 3.9, "i")                # thorax: ink ring ...
    disc(17.5, 9.0, 2.3, 2.3, "d")                # ... with a mint-dark centre
    for y in range(6, 12):                        # head
        for x in range(22, 28):
            g[y][x] = "i"
    line(24, 5, 27, 1)                            # antennae
    line(26, 5, 29, 3)
    return ["".join(r) for r in g]


_ANT_ART = _ant_art()

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


def ant_lines() -> list[list[Span]]:
    return picture_lines(_ANT_ART)


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
