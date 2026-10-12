"""Model registry + promotion rules: how newly trained algorithms are ranked, validated, managed and landed.

Status ladder (only forward, or to rejected/unstable/retired):
  candidate -> confirmed -> validated -> champion -> retired
  candidate:  came out of the search (cheap-stage evidence only)
  confirmed:  re-evaluated at a deeper stage and passed every GATE (cap, finite, beats the reference and every trivial
              forecaster, deterministic) - only through Registry.confirm(), which records the gate evidence
  validated:  beat the incumbent of its cap bin on IDENTICAL windows under a paired, dependence-aware test with family support
              (verdict 'accepted') AND the online-FDR level of that test (LORD++, recorded in `alpha_wealth`) - only through
              Registry.challenge()
  champion:   validated + a recorded sealed result for the freeze hash of exactly this spec (see gate.py)
Verdicts (cf. EvoCast's accepted/rejected/unstable/failed): a single-source or noisy gain is 'unstable', never promoted;
malformed evidence (mismatched / duplicated / missing windows, NaN, cherry-picked families) is 'failed' and changes nothing.
The forward steps cannot be taken with a bare transition(): each needs the proof its entry point recorded.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np

from . import metrics as M
from .ledger import Ledger, canon

ORDER = ["candidate", "confirmed", "validated", "champion"]
TERMINAL = {"rejected", "unstable", "retired"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS models(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, gym TEXT NOT NULL, cap_bin TEXT NOT NULL,
  spec TEXT NOT NULL, spec_key TEXT NOT NULL, status TEXT NOT NULL, origin_trial_id INTEGER, parent_model_id INTEGER,
  created_at REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS alpha_wealth(
  id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER UNIQUE NOT NULL, ts REAL, model_id INTEGER NOT NULL, incumbent_id INTEGER NOT NULL,
  alpha REAL NOT NULL, w0 REAL NOT NULL, level REAL NOT NULL, p_value REAL, effective_p REAL, promoted INTEGER NOT NULL,
  windows TEXT);
CREATE TABLE IF NOT EXISTS model_scores(
  id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, tier TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL, ci_lo REAL, ci_hi REAL, n INTEGER, ts REAL);
"""


class PromotionError(ValueError):
    """A status change was attempted without the evidence its entry point records."""


def _clean(cand: dict, inc: dict, min_windows: int):
    """Why this pair of loss dicts cannot be compared at all (None = comparable). Fail closed on everything odd."""
    if not isinstance(cand, dict) or not isinstance(inc, dict) or not cand or not inc:
        return "no evaluation windows"
    if set(cand) != set(inc):
        if not (set(cand) & set(inc)):
            return "no common evaluation windows"
        return f"family sets differ (candidate {sorted(cand)}, incumbent {sorted(inc)}): a challenger may not drop families"
    for f in sorted(cand):
        try:
            a, b = np.asarray(cand[f], float), np.asarray(inc[f], float)
        except (TypeError, ValueError):
            return f"{f}: losses are not numbers"
        if a.ndim != 1 or a.shape != b.shape:
            return f"{f}: unequal window counts {a.shape} vs {b.shape}"
        if len(a) < min_windows:
            return f"{f}: {len(a)} windows < minimum {min_windows}"
        if not (np.isfinite(a).all() and np.isfinite(b).all()):
            return f"{f}: non-finite loss"
        if (a < 0).any() or (b < 0).any():
            return f"{f}: negative loss"
        if not float(np.mean(b)) > 0:
            return f"{f}: incumbent has zero loss (degenerate reference)"
    return None


def decide_promotion(cand: dict, inc: dict, delta: float = 0.005, p_min: float = 0.95, min_families: int = 2,
                     block: int = 24, mae_ratio: float | None = None, min_windows: int | None = None) -> dict:
    """cand/inc: {family: per-window losses on IDENTICAL windows}. Returns verdict + evidence.
    accepted  pooled gain >= delta with P(improve) >= p_min, no family significantly worse, and >= min_families improve
    unstable  pooled gain is positive but the support rule fails (single source, noisy, or a family got worse)
    rejected  no credible gain
    failed    the evidence is malformed: different family sets (no cherry-picking), unequal / too few windows, NaN or
              negative loss, zero-loss incumbent. Nothing is inferred from it."""
    why = _clean(cand, inc, min_windows if min_windows is not None else 2 * block)
    if why:
        return {"verdict": "failed", "reason": why}
    fams = sorted(cand)
    per = {f: M.block_bootstrap_pimprove(cand[f], inc[f], block=block) for f in fams}
    improved = [f for f in fams if per[f]["p_improve"] >= p_min and per[f]["rel_improvement"] >= delta]
    worse = [f for f in fams if (1 - per[f]["p_improve"]) >= p_min and per[f]["rel_improvement"] <= -delta]
    # pooled with equal family weight: normalise each family by the incumbent's mean loss
    pc = np.concatenate([np.asarray(cand[f], float) / float(np.mean(inc[f])) for f in fams])
    pi = np.concatenate([np.asarray(inc[f], float) / float(np.mean(inc[f])) for f in fams])
    pooled = M.block_bootstrap_pimprove(pc, pi, block=block)
    need = min_families                                        # never relaxed to the number of families present
    reasons = []
    if mae_ratio is not None and not (mae_ratio <= 1.0 + 1e-9):   # also catches NaN
        reasons.append(f"MAE worsens (ratio {mae_ratio:.4f}); a gain only under MSE is not accepted")
    if worse:
        reasons.append(f"significantly worse on {worse}")
    if len(improved) < need:
        reasons.append(f"improves on {len(improved)}/{len(fams)} independent families, need {need}")
    if pooled["rel_improvement"] < delta:
        reasons.append(f"pooled gain {pooled['rel_improvement']:.4f} < practical threshold {delta}")
    if pooled["p_improve"] < p_min:
        reasons.append(f"P(improve)={pooled['p_improve']:.3f} < {p_min}")
    if not reasons:
        verdict = "accepted"
    elif pooled["rel_improvement"] > 0 and pooled["p_improve"] > 0.5:
        verdict = "unstable"
    else:
        verdict = "rejected"
    return {"verdict": verdict, "reasons": reasons, "pooled": pooled, "per_family": per,
            "improved_families": improved, "worse_families": worse}


class Registry:
    def __init__(self, ledger: Ledger, fdr_alpha: float = 0.05):
        self.l = ledger
        self.fdr_alpha = fdr_alpha
        ledger.conn.executescript(SCHEMA)

    def add(self, name, gym, cap_bin, spec, spec_key, origin_trial_id=None, parent=None, notes="") -> int:
        with self.l.tx() as c:
            cur = c.execute("INSERT INTO models(name,gym,cap_bin,spec,spec_key,status,origin_trial_id,parent_model_id,"
                            "created_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (name, gym, str(cap_bin), canon(spec), spec_key, "candidate", origin_trial_id, parent,
                             time.time(), notes))
            self.l._emit(c, "model_registered", {"id": cur.lastrowid, "name": name, "cap_bin": str(cap_bin),
                                                 "spec_key": spec_key, "origin_trial_id": origin_trial_id})
            return cur.lastrowid

    def register_baseline(self, name, gym, cap_bin, spec, spec_key, notes="baseline") -> int:
        """The first incumbent of a cap bin (e.g. seasonal naive, or a published model re-run by us). A deliberate, logged
        human decision: it becomes 'validated' without a challenge because there is nothing to challenge yet."""
        mid = self.add(name, gym, cap_bin, spec, spec_key, notes=notes)
        with self.l.tx() as c:
            for to in ("confirmed", "validated"):
                self._set(c, self.get(mid), to, {"baseline": True})
        return mid

    def get(self, mid: int) -> dict:
        r = self.l.conn.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        d = dict(r); d["spec"] = json.loads(d["spec"])
        return d

    def score(self, mid, tier, metric, value, ci=(None, None), n=None):
        with self.l.tx() as c:
            c.execute("INSERT INTO model_scores(model_id,tier,metric,value,ci_lo,ci_hi,n,ts) VALUES(?,?,?,?,?,?,?,?)",
                      (mid, tier, metric, value, ci[0], ci[1], n, time.time()))

    def scores(self, mid) -> list[dict]:
        return [dict(r) for r in self.l.conn.execute("SELECT * FROM model_scores WHERE model_id=? ORDER BY id", (mid,))]

    # ---- status changes -----------------------------------------------------------------------------------------
    def _set(self, c, m: dict, to: str, evidence: dict | None):
        """Apply a legal status change inside an open transaction (champion: retire the incumbent of the bin)."""
        cur, mid = m["status"], m["id"]
        legal = (cur in ORDER and to in ORDER and ORDER.index(to) == ORDER.index(cur) + 1) \
            or (to in TERMINAL and cur != "retired") or (to == "retired")
        if not legal:
            raise ValueError(f"illegal transition {cur} -> {to}")
        if to == "champion":                                   # one champion per (gym, cap bin): retire the incumbent
            for old in c.execute("SELECT id FROM models WHERE gym=? AND cap_bin=? AND status='champion' AND id!=?",
                                 (m["gym"], m["cap_bin"], mid)).fetchall():
                c.execute("UPDATE models SET status='retired' WHERE id=?", (old["id"],))
                self.l._emit(c, "model_status", {"id": old["id"], "from": "champion", "to": "retired", "replaced_by": mid})
        c.execute("UPDATE models SET status=? WHERE id=?", (to, mid))
        self.l._emit(c, "model_status", {"id": mid, "from": cur, "to": to, "evidence": evidence or {}})

    def transition(self, mid: int, to: str, evidence: dict | None = None) -> bool:
        """Public status change. Terminal moves are free; the forward steps need the proof their entry point recorded:
        confirmed <- Registry.confirm, validated <- Registry.challenge (promoted at its FDR level),
        champion <- evidence {'freeze_hash': h} where h froze exactly this model's spec and has a recorded sealed result."""
        m = self.get(mid)
        if to == "confirmed" and m["status"] == "candidate" and not self._has_gate_proof(mid):
            raise PromotionError("no recorded gate check: use Registry.confirm()")
        if to == "validated" and m["status"] == "confirmed" and not self._has_promotion_proof(mid):
            raise PromotionError("no accepted challenge recorded for this model: use Registry.challenge()")
        if to == "champion" and m["status"] == "validated":
            evidence = self._champion_evidence(m, evidence)
        with self.l.tx() as c:
            self._set(c, self.get(mid), to, evidence)
        return True

    def _has_gate_proof(self, mid) -> bool:
        return any(e["payload"].get("id") == mid and e["payload"].get("passed") for e in self.l.events("model_gates"))

    def _has_promotion_proof(self, mid) -> bool:
        return self.l.conn.execute("SELECT 1 FROM alpha_wealth WHERE model_id=? AND promoted=1", (mid,)).fetchone() is not None

    def _champion_evidence(self, m: dict, evidence: dict | None) -> dict:
        """No champion without the sealed result of exactly this spec: the freeze hash must name a frozen candidate whose spec
        equals the model's and whose look has a recorded successful result (ledger.sealed_result)."""
        fh = (evidence or {}).get("freeze_hash")
        f = self.l.frozen(m["gym"], fh) if fh else None
        if f is None:
            raise PromotionError("champion needs evidence {'freeze_hash': ...} naming a frozen candidate of this gym")
        if canon(f["candidate"]) != canon(m["spec"]):
            raise PromotionError("the frozen candidate is not this model's spec")
        res = self.l.sealed_result(m["gym"], fh)
        if res is None:
            raise PromotionError("no recorded sealed result for this freeze hash: run gate.seal first")
        return {**(evidence or {}), "freeze_hash": fh, "sealed_metrics": res["metrics"]}

    def champion(self, gym, cap_bin) -> dict | None:
        r = self.l.conn.execute("SELECT id FROM models WHERE gym=? AND cap_bin=? AND status='champion'",
                                (gym, str(cap_bin))).fetchone()
        return self.get(r["id"]) if r else None

    def incumbent(self, gym, cap_bin) -> dict | None:
        """The model a challenger must beat: the champion of the bin, else the most recently validated model."""
        ch = self.champion(gym, cap_bin)
        if ch:
            return ch
        ids = {r["id"] for r in self.l.conn.execute("SELECT id FROM models WHERE gym=? AND cap_bin=? AND status='validated'",
                                                    (gym, str(cap_bin)))}
        for e in reversed(self.l.events("model_status")):
            if e["payload"].get("to") == "validated" and e["payload"].get("id") in ids:
                return self.get(e["payload"]["id"])
        return None

    # ---- gates (candidate -> confirmed) -------------------------------------------------------------------------
    def confirm(self, mid: int, metrics: dict, replay_metrics: dict, primary: str = "rel_mse", cap_metric: str = "n_params",
                reference: float = 1.0, trivial: dict | None = None, rtol: float = 1e-9, margin: float = 1e-3) -> dict:
        """Check the gates and, if all pass, record the proof and move candidate -> confirmed. `metrics` come from the deeper
        re-evaluation, `replay_metrics` from running the same spec and seed again; `trivial` = {name: primary value of a
        no-skill forecaster (last value, mean, ...) under the same protocol}. Gates: finite, within the cap bin, beats the
        reference, beats every trivial forecaster, bit-for-bit reproducible. "Beats" means by at least `margin` (relative): the
        reference forecaster itself, run through the pipeline, scores 0.9999999995 and must not pass. A failing model stays a candidate."""
        m = self.get(mid)
        if m["status"] != "candidate":
            raise ValueError(f"confirm: model {mid} is {m['status']}, not a candidate")
        fails = []
        vals = [metrics.get(primary), metrics.get(cap_metric)]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vals):
            fails.append("primary / cap metric missing or non-finite")
        else:
            if str(m["cap_bin"]) != "0" and metrics[cap_metric] > float(m["cap_bin"]):
                fails.append(f"{cap_metric}={metrics[cap_metric]} exceeds cap bin {m['cap_bin']}")
            if not metrics[primary] < reference * (1.0 - margin):
                fails.append(f"{primary}={metrics[primary]:.4f} does not beat the reference {reference}")
            for k, v in (trivial or {}).items():
                if not metrics[primary] < v * (1.0 - margin):
                    fails.append(f"{primary}={metrics[primary]:.4f} does not beat the trivial forecaster {k!r} ({v:.4f})")
            rp = replay_metrics.get(primary)
            if not isinstance(rp, (int, float)) or not math.isfinite(rp) or abs(rp - metrics[primary]) > rtol * max(1.0, abs(rp)):
                fails.append(f"not reproducible: replay {rp} vs {metrics[primary]}")
        with self.l.tx() as c:
            safe = {k: (v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else str(v)) for k, v in metrics.items()}
            self.l._emit(c, "model_gates", {"id": mid, "passed": not fails, "fails": fails, "metrics": safe})     # ledger JSON is strict: no NaN/inf
            if not fails:
                self._set(c, self.get(mid), "confirmed", {"gates": "passed"})
        return {"passed": not fails, "fails": fails}

    # ---- promotion tests (confirmed -> validated), with online FDR control -----------------------------------------
    @staticmethod
    def loss_record(model_id: int, losses: dict, window_ids: dict, mae: dict | None = None, epoch: str = "") -> dict:
        """The payload a challenge needs: per-family per-window losses plus the hash of the ordered window identities they
        were measured on. `window_ids[f]` lists one hashable id per window (dataset, split, horizon, channel, start...)."""
        if set(losses) != set(window_ids):
            raise ValueError("every family needs both losses and window ids")
        for name, d in (("losses", losses), ("mae", mae or {})):
            for f, v in d.items():
                if len(v) != len(window_ids[f]):
                    raise ValueError(f"{name}[{f}]: {len(v)} values for {len(window_ids[f])} windows")
        return {"model_id": int(model_id), "epoch": epoch,
                "losses": {f: [float(x) for x in v] for f, v in losses.items()},
                "mae": {f: [float(x) for x in v] for f, v in (mae or {}).items()},
                "windows": {f: M.window_hash(window_ids[f]) for f in window_ids}}

    def _fdr(self) -> tuple[float, float]:
        """(alpha, w0) pinned by the FIRST recorded test: a later Registry(..., fdr_alpha=bigger) cannot loosen the procedure."""
        r = self.l.conn.execute("SELECT alpha,w0 FROM alpha_wealth ORDER BY t LIMIT 1").fetchone()
        return (r["alpha"], r["w0"]) if r else (self.fdr_alpha, self.fdr_alpha / 2)

    def challenge(self, mid: int, cand: dict, inc: dict, **kw) -> dict:
        """Promotion test of confirmed model `mid` against the incumbent of its cap bin.
        cand / inc are Registry.loss_record() payloads. Refused as 'failed' (no state change, no alpha spent) unless: the
        candidate record is for `mid`, the incumbent record is for THE incumbent of the bin (champion, else latest validated),
        both come from the same epoch, cover the same families, and each family's window-id hash is identical (same windows,
        same order). Then decide_promotion runs and, if it accepts, the pooled p-value must also be <= the LORD++ level of
        this test (online FDR over all challenges ever recorded in `alpha_wealth`)."""
        m = self.get(mid)
        why = self._record_problem(m, cand, inc)
        if why:
            self.l.event("challenge_failed", {"model_id": mid, "reason": why})
            return {"verdict": "failed", "reason": why}
        mae_ratio = float("nan")
        if cand.get("mae") and inc.get("mae") and set(cand["mae"]) == set(inc["mae"]) == set(cand["losses"]):
            rs = [float(np.mean(cand["mae"][f])) / float(np.mean(inc["mae"][f])) for f in cand["losses"]
                  if len(cand["mae"][f]) == len(inc["mae"][f]) and float(np.mean(inc["mae"][f])) > 0]
            if len(rs) == len(cand["losses"]):
                mae_ratio = M.gmre(rs)
        if math.isnan(mae_ratio):
            why = "MAE losses missing or unusable: a gain only under MSE cannot be accepted"
            self.l.event("challenge_failed", {"model_id": mid, "reason": why})
            return {"verdict": "failed", "reason": why}
        d = decide_promotion(cand["losses"], inc["losses"], mae_ratio=mae_ratio, **kw)
        if d["verdict"] == "failed":
            self.l.event("challenge_failed", {"model_id": mid, "reason": d["reason"]})
            return d
        ev = {k: v for k, v in d.items() if k in ("verdict", "reasons", "improved_families", "worse_families")}
        ev["pooled"] = dict(d["pooled"])
        p = d["pooled"]["p_value"]
        with self.l.tx() as c:                                 # one transaction: level, record and status change together
            alpha, w0 = self._fdr()
            rows = c.execute("SELECT t,promoted FROM alpha_wealth ORDER BY t").fetchall()
            t = len(rows) + 1
            level = M.lord_level(t, [r["t"] for r in rows if r["promoted"]], alpha, w0)
            passes = d["verdict"] == "accepted"
            promoted = passes and p <= level
            eff_p = p if passes else 1.0                       # side conditions failed = no evidence offered to LORD
            if passes and not promoted:
                d["verdict"] = "unstable"
                d["reasons"] = d["reasons"] + [f"pooled p={p:.3g} > online-FDR level {level:.3g} of test #{t} (alpha={alpha})"]
                ev.update(verdict="unstable", reasons=d["reasons"])
            c.execute("INSERT INTO alpha_wealth(t,ts,model_id,incumbent_id,alpha,w0,level,p_value,effective_p,promoted,windows) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (t, time.time(), mid, inc["model_id"], alpha, w0, level, p, eff_p, int(promoted), canon(cand["windows"])))
            self.l._emit(c, "alpha_wealth", {"t": t, "model_id": mid, "incumbent_id": inc["model_id"], "alpha": alpha, "w0": w0,
                                             "level": level, "p_value": p, "promoted": promoted})
            ev["alpha_level"], ev["p_value"] = level, p
            if promoted:
                self._set(c, self.get(mid), "validated", ev)
            elif d["verdict"] in ("unstable", "rejected"):
                self._set(c, self.get(mid), d["verdict"], ev)
        d["alpha_level"], d["t"] = level, t
        return d

    def _record_problem(self, m: dict, cand: dict, inc: dict) -> str | None:
        for name, r in (("candidate", cand), ("incumbent", inc)):
            if not isinstance(r, dict) or not {"model_id", "losses", "windows"} <= set(r):
                return f"{name} payload is not a Registry.loss_record()"
        if m["status"] != "confirmed":
            return f"only confirmed models can be challenged (status {m['status']})"
        if cand["model_id"] != m["id"]:
            return "candidate losses belong to a different model"
        cur = self.incumbent(m["gym"], m["cap_bin"])
        if cur is None:
            return f"no incumbent (champion / validated model) registered for cap bin {m['cap_bin']}: register_baseline first"
        if inc["model_id"] != cur["id"]:
            return f"incumbent losses are for model {inc['model_id']}, but the incumbent of cap bin {m['cap_bin']} is {cur['id']}"
        if cand.get("epoch", "") != inc.get("epoch", ""):
            return "candidate and incumbent were measured in different epochs"
        if set(cand["windows"]) != set(inc["windows"]) or set(cand["losses"]) != set(inc["losses"]) \
                or set(cand["windows"]) != set(cand["losses"]):
            return "candidate and incumbent cover different families"
        bad = [f for f in cand["windows"] if cand["windows"][f] != inc["windows"][f]]
        if bad:
            return f"window-id hashes differ on {bad}: the losses are not on identical windows"
        return None

    # ---- audit ------------------------------------------------------------------------------------------------------
    def verify(self) -> list[str]:
        """Replay the hash-chained model events and compare with the mutable `models` table; returns discrepancies. A direct
        UPDATE models SET status='champion' leaves the chain unchanged, so it shows up here."""
        expect: dict = {}
        for e in self.l.events():
            p = e["payload"]
            if e["kind"] == "model_registered":
                expect[p["id"]] = "candidate"
            elif e["kind"] == "model_status":
                expect[p["id"]] = p["to"]
        out = []
        for r in self.l.conn.execute("SELECT id,status FROM models"):
            if expect.get(r["id"]) != r["status"]:
                out.append(f"model {r['id']}: table says {r['status']!r}, events say {expect.get(r['id'])!r}")
        return out

    def table(self, gym) -> list[dict]:
        rows = self.l.conn.execute("SELECT id,name,cap_bin,status,spec_key,origin_trial_id FROM models WHERE gym=? "
                                   "ORDER BY CASE status WHEN 'champion' THEN 0 WHEN 'validated' THEN 1 WHEN 'confirmed' THEN 2 "
                                   "WHEN 'candidate' THEN 3 ELSE 4 END, id", (gym,)).fetchall()
        return [dict(r) for r in rows]
