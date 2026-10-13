"""Candidate generators ('arms'). All of them emit specs that are validated against the same Space.

random      uniform sampling                                   (floor)
tpe         Optuna TPE over the whole space, fed from ledger   (classical SOTA for hyper-parameters)
evo         tournament selection + random mutation/crossover   (deterministic evolution, no LLM)
llm         evo + a share of LLM-proposed specs                (the arm under test)
"""
from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .contract import Contract, SpecError, Space
from .credit import lever_weights


@dataclass
class Proposal:
    spec: dict
    source: str
    note: str = ""
    origin_key: str | None = None      # lineage: key of the spec this one was derived from
    edit: list | None = None           # lineage: levers changed relative to the origin


def _hist(ledger, c, arm, hist):
    if hist is not None:
        return hist
    from .history import History
    h = History(ledger, c, arm); h.update()
    return h


def rank_specs(ledger, c: Contract, arm: str, epoch: str | None = None) -> list[dict]:
    """One row per spec: its highest completed stage and that stage's value; deeper stage ranks first.
    With `epoch`, only evidence measured by that exact evaluator/contract/data identity counts."""
    by_key: dict = {}
    for t in ledger.trials(c.gym, arm, states=("done",)):
        if epoch is not None and t["epoch"] != epoch:
            continue
        m = t["metrics"] or {}
        if c.primary not in m:
            continue
        si = c.stages.index(t["stage"]) if t["stage"] in c.stages else 0
        cur = by_key.get(t["spec_key"])
        if cur is None or si > cur["stage_idx"]:
            by_key[t["spec_key"]] = {"key": t["spec_key"], "spec": t["params"], "stage_idx": si,
                                     "value": m[c.primary], "metrics": m, "trial_id": t["id"]}
    sign = 1 if c.direction == "min" else -1
    return sorted(by_key.values(), key=lambda r: (-r["stage_idx"], sign * r["value"]))


class RandomProposer:
    name = "random"

    MAX_TRIES = 50_000

    def __init__(self, c: Contract, rng: random.Random):
        self.c, self.rng = c, rng
        self.accept = None      # optional feasibility predicate (the campaign's free cost and memory filters): rejection-sample
                                # from the FEASIBLE region, so this baseline is uniform over what may be evaluated, not starved

    def propose(self, ledger, arm: str, k: int, seen: set, **kw) -> list[Proposal]:
        out, tries = [], 0
        budget = 50 * k if self.accept is None else self.MAX_TRIES
        while len(out) < k and tries < budget:
            tries += 1
            s = self.c.space.sample(self.rng)
            if self.accept is not None and not self.accept(s):
                continue
            if self.c.space.key(s) not in seen:
                seen.add(self.c.space.key(s)); out.append(Proposal(s, "random"))
        return out


class EvoProposer:
    name = "evo"

    def __init__(self, c: Contract, rng: random.Random, pool: int = 12, immigrants: float = 0.15,
                 use_credit: bool = True):
        self.c, self.rng, self.pool, self.imm, self.use_credit = c, rng, pool, immigrants, use_credit

    def propose(self, ledger, arm: str, k: int, seen: set, **kw) -> list[Proposal]:
        sp = self.c.space
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        ranked = h.ranked(self.pool)
        w = h.weights() if self.use_credit and len(ranked) >= 3 else None
        out, tries = [], 0
        while len(out) < k and tries < 80 * k:
            tries += 1
            origin = edit = None
            if len(ranked) < 3 or self.rng.random() < self.imm:
                s, src = sp.sample(self.rng), "evo:immigrant"
            else:
                a, b = sorted(self.rng.sample(range(len(ranked)), 2))     # tournament of 2 on rank
                par = ranked[a]
                if self.rng.random() < 0.25:
                    mate = ranked[self.rng.randrange(len(ranked))]["spec"]
                    s, src = sp.crossover(par["spec"], mate, self.rng), "evo:cross"
                    if self.rng.random() < 0.5:
                        s = sp.mutate(s, self.rng, 1, weights=w)
                else:
                    s, src = sp.mutate(par["spec"], self.rng, weights=w), "evo:mutate"
                origin, edit = par["key"], sp.changed(par["spec"], s)
            s = sp.validate(s)
            if sp.key(s) not in seen:
                seen.add(sp.key(s)); out.append(Proposal(s, src, origin_key=origin, edit=edit))
        return out


class TPEProposer:
    """Optuna TPE; study is rebuilt from the ledger every call, so the ledger stays the only truth.
    Uses the cheapest stage (s0) values only: higher stages are not comparable across specs."""
    name = "tpe"

    def __init__(self, c: Contract, rng: random.Random, seed: int = 0):
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        self.optuna, self.c, self.rng, self.seed = optuna, c, rng, seed
        self.accept = None      # as in RandomProposer: infeasible draws are told to the study as failed and redrawn

    def _dists(self):
        o = self.optuna.distributions
        d = {}
        for n, p in self.c.space.params.items():
            if p.kind == "int":
                d[n] = o.IntDistribution(int(p.low), int(p.high))
            elif p.kind == "float":
                d[n] = o.FloatDistribution(p.low, p.high)
            elif p.kind == "logfloat":
                d[n] = o.FloatDistribution(p.low, p.high, log=True)
            elif p.kind == "bool":
                d[n] = o.CategoricalDistribution([True, False])
            else:
                d[n] = o.CategoricalDistribution(list(p.choices))
        return d

    def propose(self, ledger, arm: str, k: int, seen: set, **kw) -> list[Proposal]:
        o, dists = self.optuna, self._dists()
        first = self.c.stages[0]
        n_done = 0
        study = o.create_study(direction="minimize" if self.c.direction == "min" else "maximize",
                               sampler=o.samplers.TPESampler(seed=self.seed + len(seen), n_startup_trials=10))
        for t in ledger.trials(self.c.gym, arm, states=("done",)):
            if t["stage"] == first and self.c.primary in (t["metrics"] or {}):
                study.add_trial(o.trial.create_trial(params=t["params"], distributions=dists,
                                                     value=t["metrics"][self.c.primary]))
                n_done += 1
        out, tries = [], 0
        budget = 30 * k if self.accept is None else 20_000
        while len(out) < k and tries < budget:
            tries += 1
            tr = study.ask(dists)
            s = self.c.space.validate(dict(tr.params))
            if self.accept is not None and not self.accept(s):
                study.tell(tr, state=o.trial.TrialState.FAIL)
                continue
            if self.c.space.key(s) not in seen:
                seen.add(self.c.space.key(s)); out.append(Proposal(s, "tpe"))
        return out


class LLMProposer:
    """Keeps a buffer of validated LLM proposals filled by background threads, so slow reasoning-model
    calls overlap with CPU evaluation. When the buffer is empty it falls back to `fallback` (evo), so
    the CPU never idles and arms stay comparable at equal evaluation budgets."""
    name = "llm"

    def __init__(self, c: Contract, client, fallback, rng: random.Random, memory_fn=None,
                 per_call: int = 6, low_water: int = 6, max_inflight: int = 4, novelty_eps: float = 0.03,
                 top_k: int = 8, llm_share: float = 1.0):
        self.c, self.client, self.fallback, self.rng = c, client, fallback, rng
        self.memory_fn, self.per_call, self.low, self.max_inflight = memory_fn, per_call, low_water, max_inflight
        self.eps, self.top_k, self.share = novelty_eps, top_k, llm_share
        self.buf: list[Proposal] = []
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_inflight)
        self.inflight = 0
        self.stats = {"calls": 0, "failed_calls": 0, "proposed": 0, "invalid": 0, "duplicate": 0, "accepted": 0}
        self._seen_specs: list[dict] = []

    # ---- prompt -------------------------------------------------------
    def build_prompt(self, ranked: list[dict], memory: str, hints: list | None = None) -> list[dict]:
        task = self.c.raw["task"].get("description", self.c.name)
        top = "\n".join(f"- {r['spec']} -> {self.c.primary}={r['value']:.5g}"
                        + (f", {self.c.cap_metric}={r['metrics'].get(self.c.cap_metric)}" if self.c.cap_metric else "")
                        for r in ranked[: self.top_k]) or "(none yet)"
        sys = ("You are a research assistant proposing candidate model specifications. "
               "Reply with ONLY a JSON object. Never include code. Every proposal must use exactly the keys and "
               "ranges listed. Prefer structurally different ideas over small numeric tweaks.")
        user = (f"Task: {task}\nObjective: {self.c.direction}imise {self.c.primary}"
                + (f" under a hard cap on {self.c.cap_metric} (bins: {self.c.caps})" if self.c.cap_metric else "")
                + f"\n\nAllowed spec keys:\n{self.c.space.describe()}\n\nBest so far:\n{top}\n"
                + (f"\nWhat we have learned (verified from logs):\n{memory}\n" if memory else "")
                + (("\nNotes from the human researcher (suggestions, not rules; the allowed keys and ranges above still apply):\n"
                    + "\n".join(f"- {h}" for h in hints) + "\n") if hints else "")
                + f"\nPropose {self.per_call} NEW, diverse specs likely to improve on the best.\n"
                  '{"proposals":[{...},...],"rationale":"one sentence"}')
        return [{"role": "system", "content": sys}, {"role": "user", "content": user}]

    # ---- background fill ---------------------------------------------
    def _job(self, messages, seen_snapshot: set, seen_specs: list):
        try:
            self._job_body(messages, seen_snapshot, seen_specs)
        except Exception as e:                                # a bad reply must never wedge the proposer (inflight is always released)
            with self.lock:
                self.stats["calls"] += 1; self.stats["failed_calls"] += 1
                self.stats["last_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        finally:
            with self.lock:
                self.inflight -= 1

    def _job_body(self, messages, seen_snapshot: set, seen_specs: list):
        obj, meta = self.client.chat_json(messages)
        good, inv, dup = [], 0, 0
        if not isinstance(obj, dict):
            with self.lock:
                self.stats["calls"] += 1; self.stats["failed_calls"] += 1
            return
        props = obj.get("proposals")
        props = props if isinstance(props, list) else []
        for raw in props[: self.per_call * 2]:
            try:
                s = self.c.space.validate(raw)
            except SpecError:
                inv += 1; continue
            k = self.c.space.key(s)
            near = min((self.c.space.distance(s, o) for o in seen_specs + [g.spec for g in good]), default=1.0)
            if k in seen_snapshot or near < self.eps:
                dup += 1; continue
            good.append(Proposal(s, f"llm:{meta.get('ep','?')}", str(obj.get("rationale", ""))[:160]))
        with self.lock:
            self.buf.extend(good)
            st = self.stats
            st["calls"] += 1; st["proposed"] += len(props); st["invalid"] += inv
            st["duplicate"] += dup; st["accepted"] += len(good)

    def _ranked_for_prompt(self, ledger, arm):
        h = getattr(self, "_h", None)
        if h is None:
            from .history import History
            h = self._h = History(ledger, self.c, arm)
        h.update()
        return h.ranked(200)

    def refill(self, ledger, arm: str, seen: set):
        with self.lock:
            need = len(self.buf) < self.low and self.inflight < self.max_inflight
            if not need:
                return
            self.inflight += 1
        ranked = self._ranked_for_prompt(ledger, arm)
        specs = [r["spec"] for r in ranked[:200]]
        memory = self.memory_fn(arm) if self.memory_fn else ""
        hints = [e["payload"]["text"] for e in ledger.recent_events("user_hint", 3)]
        self.pool.submit(self._job, self.build_prompt(ranked, memory, hints), set(seen), specs)

    def propose(self, ledger, arm: str, k: int, seen: set, **kw) -> list[Proposal]:
        self.refill(ledger, arm, seen)
        out = []
        with self.lock:
            while self.buf and len(out) < k:
                if self.rng.random() > self.share:
                    break
                p = self.buf.pop(0)
                if self.c.space.key(p.spec) in seen:
                    continue
                seen.add(self.c.space.key(p.spec)); out.append(p)
        if len(out) < k:
            out += self.fallback.propose(ledger, arm, k - len(out), seen)
        return out

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


def make_proposer(arm: str, c: Contract, seed: int, client=None, memory_fn=None, llm_share: float = 1.0):
    rng = random.Random(seed)
    if arm == "random":
        return RandomProposer(c, rng)
    if arm == "evo":
        return EvoProposer(c, rng)
    if arm == "evo_percap":
        from .portfolio import PerCapEvoProposer
        return PerCapEvoProposer(c, rng)
    if arm == "qd":
        from .portfolio import QDProposer
        return QDProposer(c, rng)
    if arm == "tpe":
        return TPEProposer(c, rng, seed)
    if arm in ("portfolio", "portfolio_llm", "portfolio_dream", "portfolio_agent"):
        from .portfolio import build_portfolio
        if arm != "portfolio" and client is None:
            raise ValueError("llm arms need a client")
        return build_portfolio(c, seed, client if arm != "portfolio" else None,
                               memory_fn if arm == "portfolio_dream" else None, agent=(arm == "portfolio_agent"))
    if arm == "agent":
        if client is None:
            raise ValueError("llm arms need a client")
        from .agent import AgentProposer
        return AgentProposer(c, client, EvoProposer(c, random.Random(seed + 1)), rng, llm_share=llm_share)
    if arm in ("llm", "llm_dream"):
        if client is None:
            raise ValueError("llm arms need a client")
        return LLMProposer(c, client, EvoProposer(c, random.Random(seed + 1)), rng,
                           memory_fn=memory_fn if arm == "llm_dream" else None, llm_share=llm_share)
    raise ValueError(f"unknown arm {arm}")
