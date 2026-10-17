"""A portfolio of global-optimisation operators and a meta-controller that allocates budget among them.

Decomposition: DISCRETE skeleton (swept exhaustively, explored by GA/ACO/LLM)  x  CONTINUOUS refinement inside a
skeleton niche (differential evolution, Gaussian distribution estimation = CMA-lite, TPE).
Global behaviour: space-filling restarts, simulated-annealing walkers that accept worse moves, tabu memory of
repeatedly bad moves, branch-and-bound-style retirement of hopeless discrete values, niche-level bandit so
under-explored skeletons are revisited ('backtracking'), and stagnation-triggered jumps.
Every operator is stateless w.r.t. restarts (derived from History) except SA walkers (soft state).
"""
from __future__ import annotations

import itertools
import math
import random

from .contract import Contract
from .proposers import EvoProposer, LLMProposer, Proposal, RandomProposer, TPEProposer, _hist


def _vec(c, spec, names):
    return {n: c.space.params[n].to_unit(spec[n]) for n in names}


def _finish(c, spec, seen, src, origin=None, parent_spec=None):
    sp = c.space
    spec = sp.validate(spec)
    k = sp.key(spec)
    if k in seen:
        return None
    seen.add(k)
    return Proposal(spec, src, origin_key=origin, edit=sp.changed(parent_spec, spec) if parent_spec else None)


class SweepProposer:
    """Exhaustive enumeration of the discrete skeleton with default continuous settings (maps the landscape)."""
    name = "sweep"

    def __init__(self, c: Contract, rng: random.Random, max_combos: int = 600):
        sk = [n for n in c.space.skeleton_names()]
        grids = [[(n, v) for v in (c.space.params[n].choices if c.space.params[n].kind == "cat" else [False, True])]
                 for n in sk]
        self.c, self.combos = c, [dict(t) for t in itertools.product(*grids)] if grids else []
        rng.shuffle(self.combos)
        self.combos = self.combos[:max_combos]
        self.i = 0

    def propose(self, ledger, arm, k, seen, **kw):
        out = []
        while self.i < len(self.combos) and len(out) < k:
            spec = {**self.c.space.defaults(), **self.combos[self.i]}
            self.i += 1
            p = _finish(self.c, spec, seen, "sweep")
            if p:
                out.append(p)
        return out


class DEProposer:
    """Differential evolution (rand/1/bin) inside a skeleton niche; discrete dims inherit from a/b/c/target."""
    name = "de"

    def __init__(self, c, rng, F=0.6, CR=0.7, min_members=5):
        self.c, self.rng, self.F, self.CR, self.minm = c, rng, F, CR, min_members

    def _pick_niche(self, h):
        cands = [(k, v) for k, v in h.niches.items() if v["n"] >= self.minm]
        if not cands:
            return None
        cands.sort(key=lambda kv: kv[1]["best"])
        w = [(1.0 / (1 + r) + 0.05) * (1.0 + h.potential(kv[0])) for r, kv in enumerate(cands)]
        return self.rng.choices(cands, w)[0][1]

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        sp, out, tries = self.c.space, [], 0
        while len(out) < k and tries < 40 * k:
            tries += 1
            nz = self._pick_niche(h)
            if nz is None:
                return out
            recs = sorted(nz["recs"], key=lambda r: h.sign * r["value"])
            elite = recs[: max(4, int(0.3 * len(recs)))]     # stateless stand-in for DE's survivor selection:
            tgt = self.rng.choice(elite)                       # difference vectors come from the elite pool, so
            a, b, cc = self.rng.sample(elite, 3)               # step sizes shrink as the niche converges
            child, jr = dict(tgt["spec"]), self.rng.choice(sp.active_names(tgt["spec"]))
            for n in sp.active_names(tgt["spec"]):
                if n in sp.skeleton_names() or not (self.rng.random() < self.CR or n == jr):
                    continue
                p = sp.params[n]
                if p.kind in ("cat", "bool"):
                    child[n] = self.rng.choice([a["spec"][n], b["spec"][n], cc["spec"][n]])
                else:
                    u = p.to_unit(a["spec"][n]) + self.F * (p.to_unit(b["spec"][n]) - p.to_unit(cc["spec"][n]))
                    child[n] = p.from_unit(u)
            pr = _finish(self.c, child, seen, "de", tgt["key"], tgt["spec"])
            if pr:
                out.append(pr)
        return out


class CEMProposer:
    """Estimation of distribution (CMA-lite): Gaussian over elite continuous dims, smoothed frequencies for discrete."""
    name = "cem"

    def __init__(self, c, rng, elite_frac=0.25, min_elites=6, inflate=1.3):
        self.c, self.rng, self.ef, self.me, self.infl = c, rng, elite_frac, min_elites, inflate

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        sp, out, tries = self.c.space, [], 0
        niches = [v for v in h.niches.values() if v["n"] >= 2 * self.me]
        if not niches:
            return out
        niches.sort(key=lambda v: v["best"])
        while len(out) < k and tries < 40 * k:
            tries += 1
            nz = niches[min(int(self.rng.expovariate(1.2)), len(niches) - 1)]
            recs = sorted(nz["recs"], key=lambda r: h.sign * r["value"])
            el = recs[: max(self.me, int(len(recs) * self.ef))]
            base = dict(el[0]["spec"])
            for n in sp.active_names(base):
                p = sp.params[n]
                if n in sp.skeleton_names():
                    continue
                if p.kind in ("cat", "bool"):
                    vals = [r["spec"][n] for r in el if n in r["spec"]]
                    cands = list(p.choices if p.kind == "cat" else [False, True])
                    base[n] = self.rng.choices(cands, [vals.count(v) + 0.5 for v in cands])[0]
                else:
                    us = [p.to_unit(r["spec"][n]) for r in el]
                    mu = sum(us) / len(us)
                    sd = max(0.03, math.sqrt(sum((u - mu) ** 2 for u in us) / len(us)) * self.infl)
                    base[n] = p.from_unit(self.rng.gauss(mu, sd))
            pr = _finish(self.c, base, seen, "cem", el[0]["key"], el[0]["spec"])
            if pr:
                out.append(pr)
        return out


class ACOProposer:
    """Ant-colony style constructive sampling from pheromone tables kept incrementally in History."""
    name = "aco"

    def __init__(self, c, rng, alpha=1.0, greedy=0.9):
        self.c, self.rng, self.alpha, self.greedy = c, rng, alpha, greedy

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        sp, out, tries = self.c.space, [], 0
        while len(out) < k and tries < 40 * k:
            tries += 1
            spec = {}
            for n, p in sp.params.items():
                if not sp.is_active(n, {**sp.defaults(), **spec}):
                    spec[n] = p.default_value(); continue
                tau = h.tau.get(n, {})
                if p.kind in ("cat", "bool"):
                    cands = list(p.choices if p.kind == "cat" else [False, True])
                    w = [tau.get(v, 1.0) ** self.alpha if self.rng.random() < self.greedy else 1.0 for v in cands]
                    spec[n] = self.rng.choices(cands, w)[0]
                else:
                    bins = list(range(8))
                    w = [tau.get(b, 1.0) ** self.alpha for b in bins] if self.rng.random() < self.greedy else [1.0] * 8
                    b = self.rng.choices(bins, w)[0]
                    spec[n] = p.from_unit((b + self.rng.random()) / 8)
            pr = _finish(self.c, spec, seen, "aco")
            if pr:
                out.append(pr)
        return out


class SAProposer:
    """Simulated-annealing walkers (soft state): accept worse moves with Metropolis probability; reheat on stagnation."""
    name = "sa"

    def __init__(self, c, rng, walkers=3, T0=0.12, cool=0.995):
        self.c, self.rng, self.W, self.T0, self.cool = c, rng, walkers, T0, cool
        self.cur: list = [None] * walkers          # (key, spec, value)
        self.pending: list = [None] * walkers      # (child_key, child_spec)
        self.steps, self.reheat = 0, 1.0

    def temperature(self):
        return self.T0 * self.reheat * (self.cool ** self.steps)

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        if h.stagnant_for > 300 and self.reheat < 4:
            self.reheat *= 2.0
        elif h.stagnant_for < 30:
            self.reheat = max(1.0, self.reheat * 0.9)
        sp, out = self.c.space, []
        elites = h.ranked(self.W)
        wts = h.weights()
        for j in range(self.W):
            if self.cur[j] is None:
                if j < len(elites):
                    e = elites[j]; self.cur[j] = (e["key"], e["spec"], h.s0.get(e["key"], e["value"]))
                continue
            pc = self.pending[j]
            if pc is not None:
                ck, cspec = pc
                if ck in h.s0:                                      # child has been evaluated: Metropolis step
                    d = (self.cur[j][2] - h.s0[ck]) / max(abs(self.cur[j][2]), 1e-9) * h.sign
                    feasible = h.first.get(ck, {}).get("feasible", True)   # a hard cap is a constraint: never walk into the infeasible region
                    if feasible and (d > 0 or self.rng.random() < math.exp(d / max(self.temperature(), 1e-6))):
                        self.cur[j] = (ck, cspec, h.s0[ck])
                    self.pending[j] = None; self.steps += 1
                else:
                    continue
            if len(out) >= k:
                continue
            scale = 0.05 + 0.25 * min(1.0, self.temperature() / self.T0)
            for _ in range(20):
                child = sp.mutate(self.cur[j][1], self.rng, scale=scale, weights=wts)
                pr = _finish(self.c, child, seen, "sa", self.cur[j][0], self.cur[j][1])
                if pr:
                    out.append(pr); self.pending[j] = (sp.key(pr.spec), pr.spec); break
        return out


class QDProposer:
    """Quality-diversity (MAP-Elites style). Parents are drawn from EVERY occupied (skeleton x cap-bin) cell rather than from
    the global top, so currently-mediocre regions keep producing stepping stones. Under-visited cells get a bonus;
    'bridge' children recombine elites of two different cells."""
    name = "qd"

    def __init__(self, c, rng, bridge=0.2, potential_weight=1.0):
        self.c, self.rng, self.bridge, self.potential_weight = c, rng, bridge, potential_weight

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        sp, out, tries = self.c.space, [], 0
        cells = list(h.cells.items())
        if len(cells) < 2:
            return out
        wts = h.weights()
        w = [(1.0 / math.sqrt(1 + h.cell_visits[c_])) * (1.0 + self.potential_weight * h.potential(c_[0]))
             for c_, _ in cells]
        while len(out) < k and tries < 40 * k:
            tries += 1
            (cell, par) = self.rng.choices(cells, w)[0]
            if self.rng.random() < self.bridge:
                (_, mate) = self.rng.choices(cells, w)[0]
                child = sp.crossover(par["spec"], mate["spec"], self.rng)
                src = "qd:bridge"
            else:
                child, src = sp.mutate(par["spec"], self.rng, weights=wts), "qd:mutate"
            pr = _finish(self.c, child, seen, src, par["key"], par["spec"])
            if pr:
                out.append(pr)
        return out


class PerCapEvoProposer:
    """FAIR BASELINE for capped-frontier search: an independent elitist evolution per parameter-cap bin (round-robin), children kept
    inside the bin by the free static cost. A single global incumbent search is a strawman here: it drifts to the largest cap."""
    name = "evo_percap"

    def __init__(self, c, rng):
        from .contract import load_static_cost
        self.c, self.rng, self.static = c, rng, load_static_cost(c.static_cost)
        self.caps, self.i = sorted(c.caps), 0

    def _in_bin(self, spec, lo, hi):
        return lo < self.static(spec) <= hi

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        sp, cm, out = self.c.space, self.c.cap_metric, []
        for _ in range(k):
            bi = self.i % len(self.caps); self.i += 1
            lo, hi = (self.caps[bi - 1] if bi else 0), self.caps[bi]
            pool = sorted((r for r in h.best.values() if lo < r["metrics"].get(cm, 0) <= hi and r["stage_idx"] == 0 or
                           (lo < r["metrics"].get(cm, 0) <= hi)), key=lambda r: h.sign * r["value"])[:12]
            for _try in range(120):
                if len(pool) < 3 or self.rng.random() < 0.1:
                    s_, par = sp.sample(self.rng), None
                else:
                    a, b = sorted(self.rng.sample(range(len(pool)), 2)); par = pool[a]
                    s_ = sp.mutate(par["spec"], self.rng)
                s_ = sp.validate(s_)
                if self.static is not None and not self._in_bin(s_, lo, hi):
                    continue
                if sp.key(s_) in seen:
                    continue
                seen.add(sp.key(s_))
                out.append(Proposal(s_, "evo_percap", origin_key=par["key"] if par else None,
                                    edit=sp.changed(par["spec"], s_) if par else None))
                break
        return out


class TPEHistProposer(TPEProposer):
    """TPE fed from the most recent first-stage records (bounded cost)."""
    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        o, dists = self.optuna, self._dists()
        study = o.create_study(direction="minimize" if self.c.direction == "min" else "maximize",
                               sampler=o.samplers.TPESampler(seed=self.seed + len(seen), n_startup_trials=10))
        for r in [x for x in h.order if x.get("feasible", True)][-1500:]:
            study.add_trial(o.trial.create_trial(params=r["spec"], distributions=dists, value=r["value"]))
        out, tries = [], 0
        while len(out) < k and tries < 30 * k:
            tries += 1
            s = self.c.space.validate(dict(study.ask(dists).params))
            if self.c.space.key(s) not in seen:
                seen.add(self.c.space.key(s)); out.append(Proposal(s, "tpe"))
        return out


class LLMStrict(LLMProposer):
    """LLM operator inside the portfolio: only returns real LLM proposals (never falls back)."""
    name = "llm"

    def propose(self, ledger, arm, k, seen, **kw):
        self.refill(ledger, arm, seen)
        out = []
        with self.lock:
            while self.buf and len(out) < k:
                p = self.buf.pop(0)
                if self.c.space.key(p.spec) not in seen:
                    seen.add(self.c.space.key(p.spec)); out.append(p)
        return out


class PortfolioProposer:
    name = "portfolio"

    def __init__(self, c: Contract, ops: dict, rng: random.Random, min_share: float = 0.04,
                 probe: float = 0.05, stagnation_n: int = 300, delayed_weight: float = 1.0, floors: dict | None = None,
                 reserve: dict | None = None, use_discount: bool = True):
        self.c, self.ops, self.rng, self.min_share, self.probe, self.stag = c, ops, rng, min_share, probe, stagnation_n
        self.floors = floors or {}                         # operator -> guaranteed share of every batch (the language-model operators: see build_portfolio)
        self.reserve = reserve or {}                       # frozenset(ops) -> guaranteed share, split equally (the stepping-stone pair)
        self.delayed_weight = delayed_weight
        self.use_discount = use_discount
        self.burst, self.burst_left, self.last_jump = 30, 0, 0
        self.stats = {"errors": {}, "filtered_tabu": 0, "filtered_retired": 0, "jumps": 0, "alloc": {}}

    def allocation(self, h, k, only=None) -> dict:
        names = [n for n in self.ops if only is None or n in only]
        theta = {}
        delayed = h.delayed_credit()
        total_succ = sum(o["succ"] for o in h.ops.values())
        for n in names:
            o = h.ops.get(n, {"n": 0, "succ": 0})
            # the bandit's Beta is over recent behaviour: discounted counts (History.ingest) weigh the last
            # trials more, so a deliberately slow-starting stepping-stone operator is not starved by its early rate
            dn, dsucc = (o.get("dn", o["n"]), o.get("dsucc", o["succ"])) if self.use_discount else (o["n"], o["succ"])
            pseudo = self.delayed_weight * delayed.get(n, 0.0) * total_succ      # lineage credit, redistributed
            theta[n] = self.rng.betavariate(1 + dsucc + pseudo, 1 + max(0, dn - dsucc))
        sw = self.ops.get("sweep")
        if sw is not None and "sweep" in theta and sw.i < len(sw.combos):
            theta["sweep"] = max(theta["sweep"], 0.5)                      # finish mapping the skeleton first
        n_seen = len(h.order)
        if (h.stagnant_for > self.stag and self.burst_left == 0
                and n_seen - self.last_jump >= self.stag // 2):            # stuck: ONE bounded burst of diversification
            self.burst_left, self.last_jump = self.burst, n_seen
            self.stats["jumps"] += 1
        b = 0
        if self.burst_left > 0:
            b = min(k, self.burst_left); self.burst_left -= b
        tot = sum(theta.values()) or 1.0
        share = {n: max(self.min_share, t / tot) for n, t in theta.items()}
        z = sum(share.values())
        probs = {n: share[n] / z for n in names}
        for ops_set, share in self.reserve.items():        # a reserve is a guarantee for a SET, split equally
            present = [n for n in names if n in ops_set]
            if len(present) < 2:
                continue
            each = share / len(present)
            others = [m for m in names if m not in present]
            othertot = sum(probs[m] for m in others)
            if othertot <= 0:                            # everything is already in the set
                for n in present:
                    probs[n] = each
                continue
            deficit = sum(max(0.0, each - probs[n]) for n in present)
            for m in others:                             # take the deficit from the others, proportionally
                probs[m] = max(0.0, probs[m] - deficit * probs[m] / othertot)
            for n in present:
                probs[n] = max(each, probs[n])
            zz = sum(probs.values()) or 1.0              # renormalise: the reserve is part of the budget, not extra
            for n in probs:
                probs[n] /= zz
        for n, f in self.floors.items():                  # a floor is a guarantee applied LAST, so it is absolute: it is never
            if n in probs and probs[n] < f:              # quietly eaten by a reserve that takes its deficit from floored ops
                rest = sum(p for m, p in probs.items() if m != n) or 1.0
                probs = {m: (f if m == n else p * (1 - f) / rest) for m, p in probs.items()}
        counts = {n: 0 for n in names}
        for _ in range(k - b):
            counts[self.rng.choices(names, [probs[n] for n in names])[0]] += 1
        div = [n for n in ("random", "aco", "qd", "sa") if n in names]
        for i in range(b if div else 0):
            counts[div[i % len(div)]] += 1
        # what this decision was, for the decision log (propose() writes it): the probability of each operator for each draw, the burst, the draws
        self.last_decision = {"policy": self.policy_tag(), "k": k, "probs": {n: round(probs[n], 4) for n in names}, "burst": b, "counts": dict(counts)}
        return counts

    def policy_tag(self) -> str:
        """The version of the allocation rule that made a decision (logged with it): a different rule is a different tag."""
        return "bandit:" + ("discounted" if self.use_discount else "plain") + (":reserve" if self.reserve else "") + (":floors" if self.floors else "")

    def _filters(self, h, props):
        retired = h.retired()
        keep = []
        for p in props:
            if h.is_tabu(p.origin_key, p.edit, p.spec):
                self.stats["filtered_tabu"] += 1; continue
            sp = self.c.space
            if any((n, p.spec[n]) in retired for n in sp.active_names(p.spec)
                   if sp.params[n].kind in ("cat", "bool")) and self.rng.random() > self.probe:
                self.stats["filtered_retired"] += 1; continue
            keep.append(p)
        return keep

    def propose(self, ledger, arm, k, seen, **kw):
        h = _hist(ledger, self.c, arm, kw.get("hist"))
        out, need, avail = [], k, set(self.ops)
        for _ in range(4):                       # operators may under-deliver: re-allocate the shortfall by posterior
            if need <= 0 or not avail:
                break
            alloc = self.allocation(h, need, avail)
            try:                                  # the decision log (roadmap step 35): never a reason to stop the search
                ledger.event("operator_alloc", {"arm": arm, **self.last_decision})
            except Exception:
                pass
            for n, m in alloc.items():
                self.stats["alloc"][n] = self.stats["alloc"].get(n, 0) + m
                if m <= 0:
                    continue
                try:
                    got = self._filters(h, self.ops[n].propose(ledger, arm, m, seen, hist=h))
                except Exception as e:           # an operator bug must never stop the lab
                    self.stats["errors"][n] = self.stats["errors"].get(n, 0) + 1
                    self.stats.setdefault("last_error", f"{n}: {type(e).__name__}: {e}"[:200])
                    avail.discard(n); continue
                if len(got) < m:
                    self.stats.setdefault("short", {})[n] = self.stats.get("short", {}).get(n, 0) + m - len(got)
                    if not got:
                        avail.discard(n)
                out += got; need -= len(got)
        if need > 0 and "random" in self.ops:    # last resort only
            out += self.ops["random"].propose(ledger, arm, need, seen)
        return out

    def close(self):
        for o in self.ops.values():
            if hasattr(o, "close"):
                o.close()


LLM_FLOOR = 0.2


def build_portfolio(c: Contract, seed: int, client=None, memory_fn=None, with_tpe=True, agent=False,
                    reserve=None, use_discount=True, drop=("sweep", "sa", "aco")) -> PortfolioProposer:
    rng = lambda i: random.Random(seed * 101 + i)
    ops = {"random": RandomProposer(c, rng(1)), "sweep": SweepProposer(c, rng(2)),
           "evo": EvoProposer(c, rng(3)), "de": DEProposer(c, rng(4)), "cem": CEMProposer(c, rng(5)),
           "aco": ACOProposer(c, rng(6)), "sa": SAProposer(c, rng(7)), "qd": QDProposer(c, rng(10))}
    if not c.space.skeleton_names():
        ops.pop("sweep")
    for name in drop:                                  # drop removes a noisy operator from the arm (it is not in the comparison otherwise)
        ops.pop(name, None)
    if with_tpe:
        try:
            ops["tpe"] = TPEHistProposer(c, rng(8), seed)
        except ImportError:
            pass
    if client is not None and agent:
        from .agent import AgentProposer                  # the agent operator takes the place of `llm`: same budget, same slot, more to read
        ops["agent"] = AgentProposer(c, client, None, rng(9), memory_fn=memory_fn)
    elif client is not None:
        ops["llm"] = LLMStrict(c, client, None, rng(9), memory_fn=memory_fn)
    # A language-model operator earns credit only when its proposals are promoted, and its proposals start weaker at the cheap stage, so the
    # bandit starves it (in eight hours 137 good calls produced two evaluated trials). To compare what a model proposes, not how the bandit
    # treats it, it is given a fixed slice of every batch. The slice is part of the arm's definition.
    floors = {k: LLM_FLOOR for k in ("llm", "agent") if k in ops}
    # The stepping-stone pair (evo + qd) is the only pair whose combination is documented to beat either alone on deceptive
    # landscapes; reserve half the budget for it, split equally, and let the bandit allocate the rest. Without the reserve the
    # bandit starves qd (it deliberately visits occupied cells, so its myopic cell-fill success rate is low) and the portfolio
    # ends up ~1.5x worse than qd alone on `trap`.
    if reserve is None and "evo" in ops and "qd" in ops:
        reserve = {frozenset({"evo", "qd"}): 0.5}
    return PortfolioProposer(c, ops, rng(0), floors=floors, reserve=reserve, use_discount=use_discount)
