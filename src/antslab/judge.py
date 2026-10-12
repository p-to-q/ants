"""Judges (System-One / discriminative decision models such as Jev, and LLMs used as classifiers) and the authority ladder.

A judge never decides alone. Every prediction is logged with the authority it had at the time (default `shadow`); later the
realised outcome is attached from deterministic evaluation; calibration (AUC, Brier, ECE) over resolved predictions decides
whether the judge may be `advisory` (reorders / prioritises) or `delegated` (filters). E0 and E7 are never delegated.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np

from .llm import http_post_json
from .textsafe import clean_text

LADDER = ("shadow", "advisory", "delegated")


# ---------------------------------------------------------------- calibration
def auc(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, int)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    r = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(r / (len(pos) * len(neg)))


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def ece(p, y, bins: int = 10) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean() for b in range(bins) if (idx == b).any()))


def calibration(ledger, judge: str, kind: str, window: int = 2000) -> dict:
    rows = [r for r in ledger.judgements(judge, kind, resolved=True, limit=window)
            if isinstance(r["p"], (int, float)) and math.isfinite(r["p"]) and 0.0 <= r["p"] <= 1.0]      # out-of-range rows carry no information
    if not rows:
        return {"n": 0}
    p = [r["p"] for r in rows]; y = [r["outcome"] for r in rows]
    return {"n": len(rows), "base_rate": float(np.mean(y)), "auc": auc(p, y), "brier": brier(p, y), "ece": ece(p, y)}


@dataclass
class Policy:
    advisory_n: int = 200
    advisory_auc: float = 0.60
    advisory_ece: float = 0.10
    delegated_n: int = 1000
    delegated_auc: float = 0.70
    delegated_ece: float = 0.05


def authority(ledger, judge: str, kind: str, policy: Policy = Policy(), never_delegate=()) -> str:
    c = calibration(ledger, judge, kind)
    if c["n"] == 0 or c["n"] < policy.advisory_n or not (c["auc"] >= policy.advisory_auc and c["ece"] <= policy.advisory_ece):
        return "shadow"
    if kind in never_delegate:
        return "advisory"
    if c["n"] >= policy.delegated_n and c["auc"] >= policy.delegated_auc and c["ece"] <= policy.delegated_ece:
        return "delegated"
    return "advisory"


# ---------------------------------------------------------------- Jev client
def _prob(x):
    """A probability or None: finite number in [0, 1] (not bool, not str). The API response is untrusted."""
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0 else None


def _find_probs(node):
    """Tolerant extraction: the exact response schema of the early-access API was not verified, so accept the plausible shapes
    ({'probabilities'|'probs'|'distribution': {...}} or a plain {option: p} map) and keep the raw response for the record.
    Every value must be a finite probability, otherwise the whole answer is treated as carrying no information."""
    def clean(d):
        if not isinstance(d, dict) or len(d) > 64:
            return None
        out = {clean_text(a, 40): _prob(b) for a, b in d.items()}
        return out if out and all(v is not None for v in out.values()) else None
    if isinstance(node, dict):
        for k in ("probabilities", "probs", "distribution", "probability"):
            if isinstance(node.get(k), dict):
                return clean(node[k])
        nums = {k: v for k, v in node.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1}
        if nums and abs(sum(nums.values()) - 1) < 1e-3:
            return clean(nums)
    return None


class JevClient:
    """Typed-decision client. Request format as documented publicly: POST {model, state, questions:{name:{type, options|min,max}}}."""
    name = "jev"

    def __init__(self, base_url="https://api.typesafe.ai/v1/systemone", key="", model="jev-latest", timeout_s=20, post=None):
        self.url, self.key, self.model, self.timeout_s = base_url, key, model, timeout_s
        self._post = post or self._http

    def _http(self, body: dict) -> dict:
        return http_post_json(self.url, body, {"Authorization": "Bearer " + self.key}, self.timeout_s)   # https only, no redirects, size cap

    def ask(self, state, questions: dict) -> tuple[dict, str]:
        """Returns ({question: {'choice','confidence','probs','score'}}, raw_json). Never raises on shape surprises."""
        raw = self._post({"model": self.model, "state": state, "questions": questions})
        node = raw.get("answers", raw.get("results", raw)) if isinstance(raw, dict) else raw
        out = {}
        for q in questions:
            a = node.get(q) if isinstance(node, dict) else None
            if a is None and isinstance(node, list):
                try:
                    a = node[list(questions).index(q)]
                except IndexError:
                    a = None
            probs = _find_probs(a) if a is not None else None
            g = a.get if isinstance(a, dict) else (lambda k: None)
            ch = g("choice")
            out[q] = {"choice": clean_text(ch, 40) if isinstance(ch, str) else None, "confidence": _prob(g("confidence")),
                      "probs": probs, "score": g("score") if isinstance(g("score"), (int, float)) and not isinstance(g("score"), bool)
                      and math.isfinite(g("score")) else None}
        return out, json.dumps(raw)[:4000]


class ProposalTriage:
    """Judge question: 'will this candidate land in the top 20% of first-stage results?' (noul / yes-no).
    Logs every prediction; acts according to the earned authority level."""
    kind = "proposal_top20"

    def __init__(self, ledger, client, task_text: str, space_text: str, policy: Policy = Policy()):
        self.l, self.c, self.task, self.space, self.policy = ledger, client, task_text, space_text, policy

    def score(self, proposals, top_summary: str, key_fn) -> list:
        """Attach p(top20) to each proposal; returns proposals ordered best-first ONLY if the judge has earned advisory rights."""
        level = authority(self.l, self.c.name, self.kind, self.policy)
        scored = []
        for pr in proposals:
            try:
                ans, raw = self.c.ask({"task": self.task, "spec_space": self.space, "best_so_far": top_summary,
                                       "candidate": pr.spec},
                                      {"top20": {"type": "noul", "instructions": "Will this candidate be among the best 20% of evaluated candidates?"}})
                probs = ans["top20"]["probs"] or {}
                p = probs.get("true", probs.get("yes", probs.get("True")))
                self.l.log_judgement(self.c.name, self.kind, key_fn(pr.spec), p, ans["top20"]["choice"], raw, level)
                scored.append((p if p is not None else 0.5, pr))
            except Exception:                         # a judge outage must never stop the lab
                scored.append((0.5, pr))
        if level == "shadow":
            return list(proposals)
        return [pr for _, pr in sorted(scored, key=lambda t: -t[0])]


def resolve_triage(ledger, hist, judge: str, kind: str = ProposalTriage.kind, top_frac: float = 0.2) -> int:
    """Attach realised outcomes: did the judged spec land in the top `top_frac` of first-stage results seen so far?"""
    n = 0
    cut = hist.percentile(top_frac)
    for r in ledger.judgements(judge, kind, resolved=False):
        v = hist.s0.get(r["subject"])
        if v is not None and cut is not None:
            ledger.resolve_judgement(r["id"], (v <= cut) if hist.sign == 1 else (v >= cut)); n += 1
    return n
