"""Hygiene for text that did not originate in our own code (LLM replies, HTTP bodies, hints, gym stderr, ledger free text).

* `clean_text` makes such text safe to PRINT: control characters (ESC sequences, CR, BEL, ...), C1 controls and bidirectional /
  invisible characters are replaced by visible escapes (`\\x1b`), so a hostile string cannot move the cursor, recolour or rewrite
  the operator's terminal, or hide itself in a log line.
* `loads_strict` parses JSON the way untrusted data must be parsed: NaN / Infinity / 1e999 / duplicate keys / absurd nesting are
  errors (`HostileJSON`), not silently accepted values.
"""
from __future__ import annotations

import json
import math
import re

_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f­​-‏ -‮⁠-⁩﻿￹-￻]")
MAX_DEPTH = 24


def _esc(m: re.Match) -> str:
    o = ord(m.group())
    return f"\\x{o:02x}" if o < 256 else f"\\u{o:04x}"


def clean_text(s, limit: int = 200) -> str:
    """str(s) with every unsafe character made visible, cut to `limit` characters (never scans more than 4*limit)."""
    s = "" if s is None else s if isinstance(s, str) else str(s)
    t = _UNSAFE.sub(_esc, s[: limit * 4])
    return t if len(t) <= limit and len(s) <= limit * 4 else t[: max(0, limit - 1)] + "…"


class HostileJSON(ValueError):
    """Syntactically valid JSON that we refuse on policy grounds."""


def _pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise HostileJSON(f"duplicate key {clean_text(k, 40)!r}")
        d[k] = v
    return d


def _constant(name):
    raise HostileJSON(f"non-finite number {name}")


def _float(s):
    v = float(s)
    if not math.isfinite(v):
        raise HostileJSON("non-finite number")
    return v


DECODER = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)


def depth_ok(obj, max_depth: int = MAX_DEPTH) -> bool:
    """Iterative (no recursion): True if obj nests at most `max_depth` containers deep."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if isinstance(o, dict):
            kids = o.values()
        elif isinstance(o, list):
            kids = o
        else:
            continue
        if d > max_depth:
            return False
        stack += [(k, d + 1) for k in kids if isinstance(k, (dict, list))]
    return True


def loads_strict(text, max_chars: int = 1 << 22):
    """json.loads with the policies above. Raises ValueError (HostileJSON for policy violations); never RecursionError."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")                       # UnicodeDecodeError is a ValueError
    if not isinstance(text, str):
        raise ValueError("not text")
    if len(text) > max_chars:
        raise HostileJSON("document too large")
    try:
        obj, end = DECODER.raw_decode(text.lstrip())
    except RecursionError:
        raise HostileJSON("nesting too deep") from None
    if text.lstrip()[end:].strip():
        raise ValueError("trailing data")
    if not depth_ok(obj):
        raise HostileJSON("nesting too deep")
    return obj
