"""A learned screen between the first and the second stage (roadmap step 43).

The quantile rule sends the best quarter of the first-stage results (per size cap) on to the stage that costs 30 s instead of 1 s. On the
server's ledgers the first stage ranks the whole range well (Spearman 0.85) but not the top of it: a hard cut discards a quarter to a third
of the near-best results (docs/research/ledger-first-look.md). This module replaces the cut by a model fitted on the arm's own finished
second-stage trials. Its inputs are the specification and the first-stage metrics; its output is a predicted second-stage value with an
uncertainty; the candidates with the best optimistic score (mean minus kappa times the spread, ranked against the last `window` candidates of
the same cap) are promoted, plus a uniformly random share `explore` of the rest, so that the model keeps seeing what it would have rejected
(and so that every decision has a known probability, which is what a later off-policy estimate needs: step 35).

The workload stays that of the quantile rule (the same expected share is promoted), so an arm that uses it differs only in WHICH
candidates are promoted. Until `min_rows` second-stage results exist the quantile rule decides. numpy only; no scikit-learn.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np

S0_FEATURES = ("rel_mse", "rel_mae", "val_mse", "n_params")        # first-stage metrics handed to the model beside the specification


class Encoder:
    """Specification + first-stage metrics -> a fixed-width vector. Numbers in the unit interval of their parameter (log scale for
    log-uniform ones), booleans 0/1, categoricals one-hot, a missing (inactive conditional) parameter -1."""

    def __init__(self, space, extra=S0_FEATURES):
        self.params = list(space.params.values())
        self.extra = tuple(extra)
        self.names = []
        for p in self.params:
            if p.kind == "cat":
                self.names += [f"{p.name}={ch}" for ch in p.choices]
            else:
                self.names.append(p.name)
        self.names += [f"s0.{k}" for k in self.extra]

    @property
    def width(self) -> int:
        return len(self.names)

    def vector(self, spec: dict, s0: dict) -> np.ndarray:
        v = []
        for p in self.params:
            x = spec.get(p.name)
            if p.kind == "cat":
                v += [1.0 if x == ch else 0.0 for ch in p.choices]
            elif x is None:
                v.append(-1.0)
            elif p.kind == "bool":
                v.append(1.0 if x else 0.0)
            else:
                try:
                    v.append(float(p.to_unit(x)))
                except (TypeError, ValueError):
                    v.append(-1.0)
        for k in self.extra:
            x = s0.get(k)
            if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
                v.append(-1.0)
            else:
                v.append(math.log10(max(float(x), 1.0)) if k == "n_params" else max(-1e3, min(1e3, float(x))))
        return np.array(v, dtype=np.float64)


class ExtraTrees:
    """Extremely randomised regression trees (Geurts et al. 2006): random split features and thresholds, the best of `n_try` by variance
    reduction, an 80% subsample per tree. The spread of the trees' predictions is the uncertainty."""

    def __init__(self, n_trees: int = 60, min_leaf: int = 3, max_depth: int = 14, n_try: int | None = None, subsample: float = 0.8, seed: int = 0):
        self.n_trees, self.min_leaf, self.max_depth, self.n_try, self.subsample = n_trees, min_leaf, max_depth, n_try, subsample
        self.rng = np.random.default_rng(seed)
        self.trees: list = []

    def fit(self, X: np.ndarray, y: np.ndarray) -> "ExtraTrees":
        n, d = X.shape
        self.n_try = self.n_try or max(2, int(round(math.sqrt(d))))
        m = max(2 * self.min_leaf, int(n * self.subsample))
        self.trees = [self._grow(X, y, self.rng.choice(n, size=min(m, n), replace=False)) for _ in range(self.n_trees)]
        return self

    def _grow(self, X, y, rows):
        feat, thr, left, right, val = [-1], [0.0], [-1], [-1], [0.0]
        stack = [(0, rows, 0)]
        while stack:
            node, idx, depth = stack.pop()
            yi = y[idx]
            m = len(idx)
            val[node] = float(yi.mean())
            if depth >= self.max_depth or m < 2 * self.min_leaf or float(yi.max()) == float(yi.min()):
                continue
            Xi = X[idx]
            lo, hi = Xi.min(0), Xi.max(0)
            cand = np.flatnonzero(hi > lo)
            if cand.size == 0:
                continue
            tot, best = float(yi.sum()), None
            for f in self.rng.choice(cand, size=min(self.n_try, cand.size), replace=False):
                t = self.rng.uniform(lo[f], hi[f])
                mask = Xi[:, f] <= t
                nl = int(mask.sum())
                if nl < self.min_leaf or m - nl < self.min_leaf:
                    continue
                sl = float(yi[mask].sum())
                score = sl * sl / nl + (tot - sl) ** 2 / (m - nl)
                if best is None or score > best[0]:
                    best = (score, int(f), float(t), mask)
            if best is None:
                continue
            _, f, t, mask = best
            li = len(feat)
            for arr, fill in ((feat, -1), (thr, 0.0), (left, -1), (right, -1), (val, 0.0)):
                arr += [fill, fill]
            feat[node], thr[node], left[node], right[node] = f, t, li, li + 1
            stack.append((li, idx[mask], depth + 1))
            stack.append((li + 1, idx[~mask], depth + 1))
        return (np.array(feat), np.array(thr), np.array(left), np.array(right), np.array(val))

    def predict(self, X: np.ndarray):
        """(mean, spread) over the trees, for each row of X."""
        X = np.atleast_2d(X)
        rows = np.arange(len(X))
        out = np.empty((len(self.trees), len(X)))
        for i, (feat, thr, left, right, val) in enumerate(self.trees):
            node = np.zeros(len(X), dtype=np.int64)
            for _ in range(self.max_depth + 1):
                f = feat[node]
                leaf = f < 0
                if leaf.all():
                    break
                go_left = X[rows, np.where(leaf, 0, f)] <= thr[node]
                node = np.where(leaf, node, np.where(go_left, left[node], right[node]))
            out[i] = val[node]
        return out.mean(0), out.std(0)


def unit(seed: int, trial_id: int) -> float:
    """A reproducible uniform draw in [0, 1) for one decision (independent of timing and of how often the process restarted)."""
    return int.from_bytes(hashlib.sha256(f"screen:{seed}:{trial_id}".encode()).digest()[:8], "big") / 2 ** 64


class ScreenPromoter:
    name = "screen"

    def __init__(self, c, seed: int = 0, explore: float = 0.10, kappa: float = 0.5, min_rows: int = 150, refit_every: int = 100,
                 window: int = 400, n_trees: int = 60):
        if not 0 <= explore < 1:
            raise ValueError("explore must be in [0, 1)")
        self.c, self.seed = c, seed
        self.explore, self.kappa, self.min_rows, self.refit_every, self.window, self.n_trees = explore, kappa, min_rows, refit_every, window, n_trees
        self.enc = Encoder(c.space)
        self.model = None
        self.fits, self.rows_at_fit, self._calls, self._scores = 0, 0, 0, {}
        self._fit_error = None

    def definition(self) -> dict:
        """Part of the arm's identity (campaign.arm_definition): a change of any of these is a different arm."""
        return {"rule": "screen", "explore": self.explore, "kappa": self.kappa, "min_rows": self.min_rows, "refit_every": self.refit_every,
                "window": self.window, "n_trees": self.n_trees, "features": list(S0_FEATURES)}

    # ---- fitting ------------------------------------------------------------------------------------------------------------
    def _eligible(self, camp) -> int:
        s1 = self.c.stages[1]
        return camp.l.conn.execute("SELECT COUNT(*) FROM trials WHERE gym=? AND arm=? AND epoch=? AND stage=? AND state='done'",
                                   (self.c.gym, camp.arm, camp.epoch, s1)).fetchone()[0]

    def _training_set(self, camp):
        import json
        s0, s1 = self.c.stages[0], self.c.stages[1]
        q = ("SELECT a.params, a.metrics, b.metrics FROM trials a JOIN trials b ON b.spec_key=a.spec_key AND b.gym=a.gym AND b.arm=a.arm AND b.epoch=a.epoch "
             "AND b.stage=? AND b.state='done' WHERE a.gym=? AND a.arm=? AND a.epoch=? AND a.stage=? AND a.state='done' ORDER BY a.id")
        X, y = [], []
        for p, m1, m0 in camp.l.conn.execute(q, (s0, self.c.gym, camp.arm, camp.epoch, s1)):
            m1, m0 = json.loads(m1 or "{}"), json.loads(m0 or "{}")
            v = m1.get(self.c.primary)
            if isinstance(v, (int, float)) and math.isfinite(v):
                X.append(self.enc.vector(json.loads(p), m0)); y.append(float(v))
        return np.array(X), np.array(y)

    def _maybe_refit(self, camp):
        self._calls += 1
        if self._calls % 20 and self.model is not None:
            return
        if self._calls % 5 and self.model is None:
            return
        n = self._eligible(camp)
        if n < self.min_rows or (self.model is not None and n - self.rows_at_fit < self.refit_every):
            return
        try:
            X, y = self._training_set(camp)
            if len(y) < self.min_rows:
                return
            self.model = ExtraTrees(self.n_trees, seed=self.seed * 1009 + self.fits).fit(X, y)
            self.fits += 1
            self.rows_at_fit = n
            camp.stats["screen_fits"] = self.fits
        except Exception as e:                       # a failing model must never stop the search: the quantile rule keeps deciding
            self._fit_error = f"{type(e).__name__}: {str(e)[:100]}"
            camp.stats["screen_fit_errors"] = camp.stats.get("screen_fit_errors", 0) + 1

    # ---- the decision -------------------------------------------------------------------------------------------------------
    def decide(self, camp, t: dict, metrics: dict, fallback) -> bool:
        """True = send this first-stage result on. `fallback()` is the quantile rule."""
        bin_ = camp.hist.cap_bin(metrics)
        self._maybe_refit(camp)
        ev = {"trial": t["id"], "bin": bin_, "fits": self.fits, "rows": self.rows_at_fit}
        if self.model is None:
            ok = bool(fallback())
            camp.l.event("screen_decision", {**ev, "mode": "cold", "promoted": ok})
            return ok
        x = self.enc.vector(t["params"], metrics)
        mu, sd = self.model.predict(x)
        score = camp.hist.sign * float(mu[0]) - self.kappa * float(sd[0])
        win = self._scores.setdefault(bin_, [])
        q = self.c.promote_quantile
        q_main = max(0.0, (q - self.explore) / (1 - self.explore))        # the main rule plus the random share promote q of all
        if len(win) < 20:
            ok, main, p, cutoff = bool(fallback()), None, None, None
        else:
            cutoff = float(np.quantile(win, q_main))
            main = score <= cutoff
            p = 1.0 if main else self.explore
            ok = main or unit(self.seed, t["id"]) < self.explore
        win.append(score)
        del win[:-self.window]
        camp.l.event("screen_decision", {**ev, "mode": "model" if p is not None else "warm", "mu": round(float(mu[0]), 5), "sd": round(float(sd[0]), 5),
                                          "score": round(score, 5), "cutoff": None if cutoff is None else round(cutoff, 5), "main": main, "p": p, "promoted": ok})
        return ok
