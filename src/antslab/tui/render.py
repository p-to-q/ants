"""Turn styled lines into text, ANSI or HTML. Curses has its own drawing in app.py; everything else (a pipe, a test, a screenshot) uses these."""
from __future__ import annotations

import html
import os
import sys

RGB = {"ink": (27, 29, 27), "mint": (168, 230, 215), "mint_dk": (92, 207, 177), "paper": (245, 245, 243), "mint_txt": (23, 145, 122),
       "ok": (63, 167, 122), "warn": (214, 158, 46), "bad": (217, 83, 79), "dim": (125, 130, 124)}
# named text styles: (foreground, bold, dim)
NAMED = {"none": (None, False, False), "ink": (None, False, False), "dim": (None, False, True), "bold": (None, True, False),
         "accent": ("mint_dk", True, False), "title": ("mint_dk", True, False), "key": ("mint_dk", True, False),
         "ok": ("ok", False, False), "warn": ("warn", True, False), "bad": ("bad", True, False), "sel": (None, True, False)}


def color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    return bool(stream.isatty()) and "NO_COLOR" not in os.environ and os.environ.get("TERM", "") not in ("", "dumb")


def plain(lines) -> str:
    return "\n".join("".join(t for t, _ in line).rstrip() for line in lines)


def _ansi_code(style) -> str:
    if isinstance(style, tuple):
        fg, bg = style
        parts = []
        if fg:
            parts.append("38;2;%d;%d;%d" % RGB[fg])
        if bg:
            parts.append("48;2;%d;%d;%d" % RGB[bg])
        return ";".join(parts)
    fg, bold, dim = NAMED.get(style, NAMED["none"])
    parts = (["1"] if bold else []) + (["2"] if dim else []) + (["38;2;%d;%d;%d" % RGB[fg]] if fg else [])
    return ";".join(parts)


def ansi(lines) -> str:
    out = []
    for line in lines:
        s = ""
        for text, style in line:
            code = _ansi_code(style)
            s += f"\x1b[{code}m{text}\x1b[0m" if code else text
        out.append(s)
    return "\n".join(out)


def to_html(lines, dark: bool = True) -> str:
    """for looking at a screen in a browser (and for the tests that check colours are reachable)"""
    css = lambda c: "rgb(%d,%d,%d)" % RGB[c]
    rows = []
    for line in lines:
        cells = ""
        for text, style in line:
            if isinstance(style, tuple):
                fg, bg = style
                if text == "▀":                      # a half block, drawn exactly (fonts leave gaps between rows)
                    top, bot = css(fg) if fg else "transparent", css(bg) if bg else "transparent"
                    st = f"color:transparent;background:linear-gradient(to bottom,{top} 50%,{bot} 50%);"
                else:
                    st = (f"color:{css(fg)};" if fg else "") + (f"background:{css(bg)};" if bg else "")
            else:
                fg, bold, dim = NAMED.get(style, NAMED["none"])
                st = (f"color:{css(fg)};" if fg else "") + ("font-weight:700;" if bold else "") + ("opacity:.6;" if dim else "")
            cells += f'<span style="{st}">{html.escape(text)}</span>'
        rows.append(cells or "&nbsp;")
    bg, fg = ("#161816", "#d9dcd7") if dark else ("#f5f5f3", "#1b1d1b")
    return (f'<pre style="background:{bg};color:{fg};font:9px/1.0 ui-monospace,Menlo,monospace;padding:16px;display:inline-block">'
            + "\n".join(rows) + "</pre>")
