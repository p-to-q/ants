"""Small animations that say 'alive' and 'doing what'. Frames come from the clock, never from a counter, so a slow redraw cannot lag them
and nothing is stored between frames. Single-width glyphs only (the interface measures cells with len()); an ASCII set for terminals that
cannot show braille. Reduced motion (ANTS_REDUCE_MOTION=1, NO_COLOR is not enough) shows one still glyph per activity."""
from __future__ import annotations

import os

# Shared activity token: new loading/working indicators reuse this template.
# Activity meaning belongs to the accompanying text and colour, not a different animation.
ACTIVITY_FRAMES = ("·", "✻", "✽", "✶", "✳", "✢")
ACTIVITY_INTERVAL = 0.16
SPINNERS = {name: (ACTIVITY_FRAMES, ACTIVITY_INTERVAL)
            for name in ("loading", "running", "model", "found", "alive")}
ASCII = dict.fromkeys(SPINNERS, "|/-\\")
STILL = {"loading": "…", "running": "●", "model": "…", "found": "★", "alive": "●"}


def reduced_motion() -> bool:
    return os.environ.get("ANTS_REDUCE_MOTION", "") not in ("", "0")


def slow_link() -> bool:
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def frame(name: str, t: float, ascii_only: bool = False, still: bool | None = None) -> str:
    """the glyph(s) of spinner `name` at clock time `t` seconds. Over ssh the interval is doubled (fewer cells change per second)."""
    if (reduced_motion() if still is None else still):
        return ("*" if name == "found" else ".") if ascii_only else STILL[name]
    frames, every = SPINNERS[name]
    if ascii_only:
        frames = ASCII[name]
    if slow_link():
        every *= 2
    return frames[int(t / every) % len(frames)]


def pulse(t: float, period: float = 1.6) -> bool:
    """the heartbeat of the mark: its cursor pixel is lit for the first third of every period"""
    return reduced_motion() or (t % period) < period / 3
