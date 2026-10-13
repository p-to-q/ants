"""Plain-language status: how a search is going, in one short phrase and one sentence, from numbers that are already measured.

The phrase never replaces the numbers (they stay next to it), always names its reason, says what to do when something needs a look, and
cheers only for real events. Pure: everything comes in as a dict, so every condition has a test.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Mood:
    phrase: str
    style: str          # a views style name: ok, warn, bad, accent, dim
    reason: str
    glyph: str = "●"    # the animation to use: running, found, model, alive, or '' for a still dot


def _age(s: float) -> str:
    return f"{s:.0f}s" if s < 90 else f"{s / 60:.0f} min" if s < 5400 else f"{s / 3600:.1f} h"


def mood(i: dict) -> Mood:
    """i: paused, chain_ok, free_gb, evals, finished_1h, rate_median_1h (finished per hour, median over the last 6 h), last_age_s,
    fail_share (of the last 40 finished), best_age_s (since the last new best, None if never), llm_fail_share (last hour, None if no calls),
    backup_age_h (None if unknown)"""
    if i.get("paused"):
        return Mood("Paused", "warn", "paused by the PAUSE flag; press p to resume", "")
    if i.get("chain_ok") is False:
        return Mood("Needs a look", "bad", "the hash chain is broken: open Health", "")
    if (i.get("free_gb") or 99) < 5:
        return Mood("Running low on space", "bad", f"{i['free_gb']:.1f} GB free", "running")
    if not i.get("evals"):
        return Mood("Warming up", "dim", "no evaluation has finished yet", "loading")
    if (i.get("backup_age_h") or 0) > 48:
        return Mood("Needs a look", "warn", f"the last backup is {i['backup_age_h']:.0f} h old: open Health", "running")
    age = i.get("last_age_s")
    if age is not None and age > 1800 and not i.get("paused"):
        return Mood("Stuck?", "bad", f"nothing has finished for {_age(age)}", "")
    if (i.get("fail_share") or 0) > 0.25:
        return Mood("Needs a look", "bad", f"{i['fail_share']:.0%} of the last 40 evaluations failed", "running")
    ba = i.get("best_age_s")
    if ba is not None and ba < 600:
        return Mood("Just found something", "ok", f"a new best {_age(ba)} ago", "found")
    if (i.get("llm_fail_share") or 0) > 0.5:
        return Mood("Waiting on the model", "warn", f"{i['llm_fail_share']:.0%} of model calls failed this hour; the search carries on without", "model")
    rate, med = i.get("finished_1h") or 0, i.get("rate_median_1h") or 0
    if med and rate < 0.3 * med:
        return Mood("A bit quiet", "warn", f"{rate} finished this hour against {med:.0f} usually", "running")
    if med and rate < 0.7 * med:
        return Mood("A bit slow", "warn", f"{rate} finished this hour against {med:.0f} usually", "running")
    if ba is not None and ba < 3600:
        return Mood("Going well", "ok", f"{rate} evaluations this hour, last new best {_age(ba)} ago", "running")
    if ba is None or ba > 6 * 3600:
        return Mood("Exploring", "accent", f"{rate} evaluations this hour, no new best for {_age(ba) if ba else 'a while'}", "running")
    return Mood("Steady", "ok", f"{rate} evaluations this hour, last new best {_age(ba)} ago", "running")


def sentence(m: Mood) -> str:
    return f"{m.phrase}: {m.reason}"
