"""Incremental in-memory view of one arm's finished trials.

Re-reading and JSON-parsing every trial on every proposal is O(n^2) over a run; at tens of thousands of trials a day that is
unusable. History tails the ledger (by finished_at) and keeps exactly what proposers need: best record per spec,
first-stage values, lever credit and per-operator success statistics, all updated incrementally.
"""
from __future__ import annotations

import bisect
import heapq
import json
import math

from .contract import Contract


class History:
    def __init__(self, ledger, c: Contract, arm: str, epoch: str | None = None, max_records: int = 60_000):
        self.l, self.c, self.arm = ledger, c, arm
        self.epoch, self.max_records = epoch, max_records       # epoch=None: ingest every epoch (tools/tests)
        self.cursor, self.cursor_id = -1.0, -1
        self.stage_sorted: dict = {}                            # stage index -> sorted signed values (feasible)
        self.best: dict = {}            # spec_key -> record at the deepest stage reached
        self.s0: dict = {}              # spec_key -> first-stage value
        self.s0_sorted: list = []       # sorted first-stage values (for running quantiles)
        self.credit: dict = {}          # lever -> {n, gain, wins}
        self.ops: dict = {}             # operator -> {n, succ}
        self.moves: list = []           # recent (lever, bin, delta)
        self.tabu: dict = {}            # (origin_key, lever, bin) -> expires at this evaluation count
        self.first: dict = {}           # spec_key -> first-stage record
        self.order: list = []           # first-stage records in ingestion order
        self.val_stats: dict = {}       # (lever, value) -> [n, best_signed] for discrete levers
        self.niches: dict = {}          # skeleton signature -> {n, best_signed, recs}
        self.traj: dict = {}            # skeleton signature -> best-so-far (signed) after each of its evaluations
        self.cells: dict = {}           # (skeleton signature, cap bin) -> best first-stage record (MAP-Elites archive)
        self.cell_visits: dict = {}
        self.best_s0 = None             # best signed first-stage value and when it was found
        self.best_n = 0
        self.tau: dict = {}             # ACO pheromone: lever -> {bin: level}
        self._batch: list = []
        self.n = 0
        self.version = 0
        self.sign = 1 if c.direction == "min" else -1

    # ---- ingestion ----------------------------------------------------
    BATCH = 20_000

    def update(self) -> int:
        """Ingest everything finished since the last call (in batches, so memory stays bounded).

        Readers (board, best, top --once) depend on this being complete: a bounded call would silently show only the oldest rows."""
        total = 0
        while True:
            n = self._update_batch()
            total += n[0]
            if n[1] < self.BATCH:
                return total

    def _update_batch(self) -> tuple:
        q = ("SELECT id,spec_key,stage,params,metrics,source,origin_key,edit,finished_at FROM trials "
             "WHERE gym=? AND arm=? AND state='done' AND (finished_at>? OR (finished_at=? AND id>?))"
             + (" AND epoch=?" if self.epoch is not None else "") + " ORDER BY finished_at,id LIMIT " + str(self.BATCH))
        args = [self.c.gym, self.arm, self.cursor, self.cursor, self.cursor_id] + ([self.epoch] if self.epoch is not None else [])
        rows = self.l.conn.execute(q, args).fetchall()
        added = 0
        for r in rows:
            self.cursor, self.cursor_id = (r["finished_at"] or 0), r["id"]
            m = json.loads(r["metrics"]) if r["metrics"] else {}
            if self.c.primary not in m:
                continue
            added += 1
            si = self.c.stages.index(r["stage"]) if r["stage"] in self.c.stages else 0
            rec = {"id": r["id"], "key": r["spec_key"], "stage_idx": si, "value": m[self.c.primary],
                   "metrics": m, "spec": json.loads(r["params"]), "source": r["source"],
                   "origin_key": r["origin_key"], "edit": json.loads(r["edit"]) if r["edit"] else None}
            rec["feasible"] = self.feasible(m)
            if rec["feasible"]:
                bisect.insort(self.stage_sorted.setdefault(si, []), self.sign * rec["value"])
            cur = self.best.get(rec["key"])
            if rec["feasible"] and (cur is None or si > cur["stage_idx"]):
                self.best[rec["key"]] = rec
            if si == 0:
                self._ingest_first_stage(rec)
        self.n += added
        if added:
            self.version += 1
            if len(self.order) > self.max_records:
                self._evict()
        return added, len(rows)

    def stage_cut(self, si: int, q: float):
        """Signed value at quantile q of a stage's feasible results (None while empty)."""
        v = self.stage_sorted.get(si, [])
        return v[max(0, int(len(v) * q) - 1)] if v else None

    def _evict(self):
        """Bound memory for multi-week runs: forget old per-trial records (the ledger keeps everything); aggregates stay."""
        drop = len(self.order) // 5
        gone, self.order = self.order[:drop], self.order[drop:]
        keep_top = {r["key"] for r in heapq.nsmallest(min(2000, max(10, self.max_records // 10)), self.first.values(), key=lambda r: self.sign * r["value"])}
        for r in gone:
            if r["key"] not in keep_top:
                self.first.pop(r["key"], None); self.s0.pop(r["key"], None)
        for nz in self.niches.values():
            if len(nz["recs"]) > 3000:
                nz["recs"] = sorted(nz["recs"], key=lambda r: self.sign * r["value"])[:200] + nz["recs"][-2800:]
        if len(self.best) > 40_000:
            self.best = {r["key"]: r for r in heapq.nsmallest(20_000, self.best.values(), key=lambda r: (-r["stage_idx"], self.sign * r["value"]))}
        if len(self.moves) > 400:
            del self.moves[:100]
        self.tabu = {k: v for k, v in self.tabu.items() if v > len(self.order)}
        for lst in self.traj.values():
            if len(lst) > 20_000:
                del lst[:len(lst) // 2]

    def _ingest_first_stage(self, rec):
        v = rec["value"]
        self.s0[rec["key"]] = v
        self.first[rec["key"]] = rec
        self.order.append(rec)
        sv = self.sign * v
        sp = self.c.space
        ok = rec["feasible"]
        if ok and (self.best_s0 is None or sv < self.best_s0 - 1e-3 * abs(self.best_s0)):
            self.best_s0, self.best_n = sv, len(self.order)
        if ok:
            for n in sp.active_names(rec["spec"]):
                if sp.params[n].kind in ("cat", "bool"):
                    st = self.val_stats.setdefault((n, rec["spec"][n]), [0, float("inf")])
                    st[0] += 1; st[1] = min(st[1], sv)
        cell = (sp.niche(rec["spec"]), self.cap_bin(rec["metrics"]))
        self.cell_visits[cell] = self.cell_visits.get(cell, 0) + 1
        cur = self.cells.get(cell)
        improved = cur is None or sv < self.sign * cur["value"]      # decided BEFORE the archive is updated
        if improved:
            self.cells[cell] = rec
        if ok:
            nz = self.niches.setdefault(sp.niche(rec["spec"]), {"n": 0, "best": float("inf"), "recs": []})
            nz["n"] += 1; nz["best"] = min(nz["best"], sv); nz["recs"].append(rec)
            self.traj.setdefault(sp.niche(rec["spec"]), []).append(nz["best"])
            self._batch.append(rec)
            if len(self._batch) >= 20:
                self._pheromone_round()
            bisect.insort(self.s0_sorted, self.sign * v)
        # operator success = ARCHIVE IMPROVEMENT: the child beats the elite of its (skeleton x cap-bin) cell or opens a new
        # cell. 'In the top 15% of history' is too easy: history is mostly random junk, so any exploiter clears it.
        op = rec["source"].split(":")[0]
        o = self.ops.setdefault(op, {"n": 0, "succ": 0})
        o["n"] += 1
        o["succ"] += bool(improved and ok)
        if rec["origin_key"] and rec["edit"] and rec["origin_key"] in self.s0:
            pv = self.s0[rec["origin_key"]]
            d = (pv - v) / max(abs(pv), 1e-9) * self.sign
            d = max(-1.0, min(1.0, d))
            for k in rec["edit"]:
                s = self.credit.setdefault(k, {"n": 0, "gain": 0.0, "wins": 0})
                s["n"] += 1; s["gain"] += max(d, 0.0) / len(rec["edit"]); s["wins"] += d > 0
                self.moves.append((k, self._bin(k, rec["spec"].get(k)), d))
                if d < 0:                       # tabu = do not repeat THIS failed step from THIS parent for a while
                    self.tabu[(rec["origin_key"], k, self._bin(k, rec["spec"].get(k)))] = len(self.order) + 40
            if len(self.moves) > 400:
                del self.moves[:100]

    def _pheromone_round(self, rho: float = 0.1, top: int = 3):
        for lever in self.c.space.params:
            for b in self.tau.setdefault(lever, {}):
                self.tau[lever][b] *= (1 - rho)
        batch = sorted(self._batch, key=lambda r: self.sign * r["value"])[:top]
        for rank, r in enumerate(batch):
            for n in self.c.space.active_names(r["spec"]):
                b = self._bin(n, r["spec"][n])
                self.tau[n][b] = self.tau[n].get(b, 1.0) + 1.0 / (rank + 1)
        self._batch = []

    def feasible(self, metrics: dict) -> bool:
        """A hard cap is a CONSTRAINT: over-cap records stay in the lineage/archive as stepping stones, but they are
        never leaders, elites, parents-by-rank or reward signals."""
        cm, caps = self.c.cap_metric, self.c.caps
        return not (cm and caps and metrics.get(cm, 0) > max(caps))

    def cap_bin(self, metrics: dict) -> float:
        cm, caps = self.c.cap_metric, sorted(self.c.caps)
        if not cm or not caps or cm not in metrics:
            return 0
        return next((b for b in caps if metrics[cm] <= b), float("inf"))

    def trajectory(self, niche, w: int = 15) -> dict:
        """Dynamics of one research path (skeleton niche): level, velocity, acceleration of its best-so-far curve.
        velocity     = relative improvement per evaluation over the last w evaluations
        acceleration = change of that velocity versus the w evaluations before (positive = improving faster)
        A leader is judged by level; a riser by velocity/acceleration. They are different things."""
        b = self.traj.get(niche, [])
        n = len(b)
        out = {"n": n, "level": (b[-1] if b else None), "velocity": 0.0, "acceleration": 0.0}
        if n >= w + 1:
            scale = max(abs(b[-1]), 1e-9)
            v1 = (b[-1 - w] - b[-1]) / w / scale
            out["velocity"] = v1
            if n >= 2 * w + 1:
                v0 = (b[-1 - 2 * w] - b[-1 - w]) / w / scale
                out["acceleration"] = v1 - v0
        return out

    def potential(self, niche, w: int = 15) -> float:
        """Optimistic headroom of a path: young paths get a high prior; mature ones are scored by their dynamics.
        Always >= 0. Used to allocate exploration, never to rank the final answer (that is level only)."""
        t = self.trajectory(niche, w)
        if t["n"] < w + 1:
            return 1.0
        return max(0.0, 20.0 * t["velocity"] + 20.0 * max(0.0, t["acceleration"]))

    def delayed_credit(self, top: int = 20, decay: float = 0.92, refresh: int = 25) -> dict:
        """Credit flows BACK along lineage: every ancestor of a current top record credits the operator that produced
        it (discounted by distance). This values stepping-stone operators whose payoff is late, which a myopic
        'did the child land in the top 15% now?' reward starves."""
        if getattr(self, "_dc_n", -999) > len(self.order) - refresh and hasattr(self, "_dc"):
            return self._dc
        out: dict = {}
        for rec in sorted(self.first.values(), key=lambda r: self.sign * r["value"])[:top]:
            r, depth, seen = rec, 0, set()
            while r and r["key"] not in seen and depth < 60:
                seen.add(r["key"])
                op = r["source"].split(":")[0]
                out[op] = out.get(op, 0.0) + decay ** depth
                r = self.first.get(r["origin_key"]) if r["origin_key"] else None
                depth += 1
        tot = sum(out.values()) or 1.0
        self._dc = {k: v / tot for k, v in out.items()}
        self._dc_n = len(self.order)
        return self._dc

    @property
    def stagnant_for(self) -> int:
        return len(self.order) - self.best_n

    def retired(self, q: float = 0.4, min_n: int = 12) -> set:
        """Branch-and-bound-style pruning: a discrete lever value is retired when even the best of >= min_n tries
        cannot reach the q-quantile of everything seen. Reversible (recomputed each call) and probed at low rate."""
        cut = self.percentile(q)
        if cut is None:
            return set()
        cut_s = self.sign * cut
        return {k for k, (n, best) in self.val_stats.items() if n >= min_n and best > cut_s}

    def _bin(self, lever, value):
        p = self.c.space.params.get(lever)
        if p is None or p.kind in ("cat", "bool"):
            return value
        lo, hi = p.low, p.high
        if p.kind == "logfloat":
            x = (math.log(value) - math.log(lo)) / (math.log(hi) - math.log(lo))
        else:
            x = (value - lo) / (hi - lo) if hi > lo else 0.0
        return min(7, max(0, int(x * 8)))

    # ---- queries --------------------------------------------------------
    def ranked(self, top: int | None = None) -> list[dict]:
        key = lambda r: (-r["stage_idx"], self.sign * r["value"])
        recs = self.best.values()
        return sorted(recs, key=key) if top is None else heapq.nsmallest(top, recs, key=key)

    def weights(self, base: float = 0.02, explore: float = 0.05) -> dict:
        total = sum(s["n"] for s in self.credit.values())
        out = {}
        for n in self.c.space.params:
            s = self.credit.get(n, {"n": 0, "gain": 0.0})
            out[n] = base + s["gain"] / (s["n"] + 2) + explore * math.sqrt(math.log(total + 2) / (s["n"] + 1))
        return out

    def percentile(self, q: float) -> float | None:
        if not self.s0_sorted:
            return None
        return self.sign * self.s0_sorted[min(len(self.s0_sorted) - 1, int(q * len(self.s0_sorted)))]

    def is_tabu(self, origin_key, edit, spec) -> bool:
        """Short-term memory against cycling: the same failed single-lever step from the same parent."""
        if not origin_key or not edit:
            return False
        now = len(self.order)
        return any(self.tabu.get((origin_key, k, self._bin(k, spec.get(k))), -1) > now for k in edit)
