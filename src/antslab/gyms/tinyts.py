"""TinyTS: a closed-form-trainable family of ultra-light time-series forecasters (numpy only).

Why closed form: every core below is (reduced-rank / structured) linear in its parameters, so training is
one ridge solve -> milliseconds to seconds per evaluation on 2 CPU cores, fully deterministic.
That is what makes a 2-3 day 'first signs' cadence possible.

Cores     linear | period (SparseTSF-style period folding, one shared map) | fourier (FITS-style low-pass
          spectrum map) | basis (reduced-rank regression, TimeBase-style shared basis)
Layers    normalisation (none/last/mean/revin) -> optional MA trend head -> optional seasonal-naive skip
          -> core -> post-training (magnitude pruning, int8/int4 weight quantisation)
Cost      n_params / bytes are always reported at PARAM_H=720 so hard caps are comparable across stages.

Data: synthetic ETT-like series by default (pipeline/speed validation ONLY, not research evidence);
real ETT-format CSVs via --data <dir>.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

PARAM_H = 720
MAX_WINDOW_ELEMS = int(__import__('os').environ.get('ANTS_MAX_WINDOW_ELEMS', 4_000_000))      # float64 elements per window matrix (64 MB). Evaluation memory is part of the cost model:
                                  # the 2 GB control tower runs with MemoryMax=950M, so a spec must not be able to ask for more.
LEVER_DEFAULTS = dict(clip=False, clip_z=4.0, recency=0.0, augment=False, aug_sigma=0.05, robust=False,
                      pretrain="none", prior_alpha=1.0, adapter="none", adapter_rank=2, distill_lambda=0.0, ens=1)


def g(spec, key):
    return spec.get(key, LEVER_DEFAULTS[key])
SPLIT = (8640, 2880, 2880)                      # ETTh1 chronological 12/4/4 months
STAGES = {
    "s0": dict(seeds=[0], H=[96], tr=4, va=12),
    "s1": dict(seeds=[0, 1, 2], H=[96, 192, 336, 720], tr=2, va=6),
    "s2": dict(seeds=[0, 1, 2, 3, 4, 5], H=[96, 192, 336, 720], tr=1, va=3),
    "final": dict(seeds=[100, 101, 102, 103, 104, 105], H=[96, 192, 336, 720], tr=1, va=1),
}
# 'hard' profile: series with spikes, level shifts and seasonal-amplitude drift (seed % 1000 >= 200), so data-centric
# levers (clipping, recency, robust regression) have something to fix. Still synthetic: pipeline validation only.
STAGES_HARD = {
    "s0": dict(seeds=[200], H=[96], tr=4, va=12),
    "s1": dict(seeds=[200, 201, 202], H=[96, 192, 336, 720], tr=2, va=6),
    "s2": dict(seeds=[200, 201, 202, 203, 204, 205], H=[96, 192, 336, 720], tr=1, va=3),
    "final": dict(seeds=[300, 301, 302, 303, 304, 305], H=[96, 192, 336, 720], tr=1, va=1),
}
PROFILES = {"clean": (STAGES, (1000, 1001, 1002)), "hard": (STAGES_HARD, (1200, 1201, 1202))}
_CACHE: dict = {}


# ---------------------------------------------------------------- data
def synth(seed: int, n: int = sum(SPLIT), ch: int = 7) -> np.ndarray:
    if seed in _CACHE:
        return _CACHE[seed]
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    out = np.zeros((n, ch))
    for c in range(ch):
        s = sum(rng.uniform(lo, hi) * np.sin(2 * np.pi * t / per + rng.uniform(0, 6.28))
                for per, lo, hi in ((24, .5, 1.5), (168, .2, 1.0), (12, 0., .4)))
        e = rng.normal(0, .35, n)
        ar = np.zeros(n)
        for i in range(1, n):
            ar[i] = .85 * ar[i - 1] + e[i]
        out[:, c] = s + np.cumsum(rng.normal(0, .003, n)) + ar
    if seed % 1000 >= 200:                                          # hard profile corruptions
        sd = out.std(0)
        for c in range(ch):
            drift = 1 + 0.3 * np.sin(2 * np.pi * t / 6000 + rng.uniform(0, 6.28))
            out[:, c] = out[:, c] * drift
            for cp in rng.choice(np.arange(1000, n - 1000), 3, replace=False):
                out[cp:, c] += rng.normal(0, 1.0) * sd[c]
            k = rng.random(n) < 0.004
            out[k, c] += rng.normal(0, 6.0, k.sum()) * sd[c]
    _CACHE[seed] = out
    return out


def load_ett(path: Path) -> np.ndarray:
    rows = [l.strip().split(",")[1:] for l in Path(path).read_text().splitlines()[1:] if l.strip()]
    return np.asarray(rows, dtype=np.float64)


def get_series(key, data_dir):
    if data_dir:
        return load_ett(Path(data_dir) / f"{key}.csv")
    return synth(int(key))


def windows(data, lo, hi, L, H, stride, with_t=False):
    """All channel-independent (input, target) windows whose TARGET lies in [lo, hi)."""
    xs, ys, ts = [], [], []
    n_w = max(1, (hi - max(lo, L) - H + 1) // max(1, stride)) * data.shape[1]
    if n_w * (L + H) > MAX_WINDOW_ELEMS:                          # deterministic thinning instead of an unbounded matrix
        stride = int(np.ceil(stride * n_w * (L + H) / MAX_WINDOW_ELEMS))
    starts = np.arange(max(lo, L), hi - H + 1, stride)
    for c in range(data.shape[1]):
        col = data[:, c]
        idx = starts[:, None] + np.arange(-L, H)[None, :]
        w = col[idx]
        xs.append(w[:, :L]); ys.append(w[:, L:])
        ts.append((starts - lo) / max(1, hi - lo))
    out = (np.concatenate(xs), np.concatenate(ys))
    return out + (np.concatenate(ts),) if with_t else out


# ---------------------------------------------------------------- cores
def ridge(X, Y, alpha, W0=None, alpha0=0.0):
    """min ||Y - XW||^2 + alpha*||W||^2 + alpha0*||W - W0||^2  (alpha0/W0 = fine-tune towards a pre-trained prior)."""
    A = X.T @ X
    sc = max(1.0, np.trace(A) / A.shape[0])
    A[np.diag_indices_from(A)] += (alpha + alpha0) * sc
    rhs = X.T @ Y
    if W0 is not None and alpha0 > 0:
        rhs = rhs + alpha0 * sc * W0
    return np.linalg.solve(A, rhs)


def eff_len(L, p):
    return max(p, (L // p) * p)


class Core:
    mats: dict

    def count(self, H):                                   # analytic, unpruned
        raise NotImplementedError


class LinearCore(Core):
    def __init__(s, L, p, alpha, **kw): s.L, s.alpha, s.mats = L, alpha, {}
    def fit(s, X, Y, prior=None, a0=0.0):
        s.mats["W"] = ridge(X, Y, s.alpha, prior["W"] if prior else None, a0)
    def predict(s, X, H=None): return X @ s.mats["W"]
    @staticmethod
    def n(L, p, H, **kw): return L * H


class PeriodCore(Core):
    def __init__(s, L, p, alpha, **kw): s.L, s.p, s.alpha, s.mats = L, p, alpha, {}
    def fit(s, X, Y, prior=None, a0=0.0):
        p, m, H = s.p, s.L // s.p, Y.shape[1]
        K = -(-H // p)
        Z = [X[:, j::p] for j in range(p)]
        A = np.zeros((K, m))
        A0 = prior["A"] if prior else None
        full = H // p
        if full:
            XtX = sum(z.T @ z for z in Z)
            sc = max(1.0, np.trace(XtX) / m)
            XtX[np.diag_indices_from(XtX)] += (s.alpha + a0) * sc
            rhs = sum(Z[j].T @ Y[:, j::p][:, :full] for j in range(p))
            if A0 is not None and a0 > 0:
                rhs = rhs + a0 * sc * A0[:full].T
            A[:full] = np.linalg.solve(XtX, rhs).T
        if H % p:
            js = [j for j in range(p) if j + full * p < H]
            XtX = sum(Z[j].T @ Z[j] for j in js)
            sc = max(1.0, np.trace(XtX) / m)
            XtX[np.diag_indices_from(XtX)] += (s.alpha + a0) * sc
            rhs = sum(Z[j].T @ Y[:, j + full * p] for j in js)
            if A0 is not None and a0 > 0:
                rhs = rhs + a0 * sc * A0[full]
            A[full] = np.linalg.solve(XtX, rhs)
        s.mats["A"] = A
    def predict(s, X, H=None):
        A, p = s.mats["A"], s.p
        H = H or A.shape[0] * p
        out = np.zeros((X.shape[0], H))
        for j in range(min(p, H)):                          # one matmul per phase, not per horizon step
            kj = (H - j + p - 1) // p
            out[:, j::p] = X[:, j::p] @ A[:kj].T
        return out
    @staticmethod
    def n(L, p, H, **kw): return -(-H // p) * (L // p)


class FourierCore(Core):
    def __init__(s, L, p, alpha, k=16, **kw): s.L, s.K, s.alpha, s.mats = L, min(k, L // 2 + 1), alpha, {}
    def _feat(s, X):
        F = np.fft.rfft(X, axis=1)[:, : s.K]
        return np.concatenate([F.real, F.imag], 1)
    def _kout(s, H): return min(-(-s.K * (s.L + H) // s.L), (s.L + H) // 2 + 1)
    def fit(s, X, Y, prior=None, a0=0.0):
        H = Y.shape[1]; T = s.L + H; ko = s._kout(H)
        S = np.fft.rfft(np.concatenate([X, Y], 1), axis=1)[:, :ko]
        s.mats["W"] = ridge(s._feat(X), np.concatenate([S.real, S.imag], 1), s.alpha,
                            prior["W"] if prior else None, a0)
        s.H = H
    def predict(s, X, H=None):
        H = H or s.H; T = s.L + H; ko = s._kout(H)
        W = s.mats["W"]
        out = s._feat(X) @ W[:, : 2 * ko] if W.shape[1] >= 2 * ko else s._feat(X) @ W
        full = np.zeros((X.shape[0], T // 2 + 1), dtype=complex)
        k = out.shape[1] // 2
        full[:, :k] = out[:, :k] + 1j * out[:, k:]
        return np.fft.irfft(full, n=T, axis=1)[:, -H:]
    @staticmethod
    def n(L, p, H, k=16, **kw):
        K = min(k, L // 2 + 1); ko = min(-(-K * (L + H) // L), (L + H) // 2 + 1)
        return 2 * K * 2 * ko


class BasisCore(Core):
    def __init__(s, L, p, alpha, r=8, **kw): s.L, s.r, s.alpha, s.mats = L, r, alpha, {}
    def fit(s, X, Y, prior=None, a0=0.0):
        W = ridge(X, Y, s.alpha)
        r = min(s.r, W.shape[1], X.shape[1])
        _, _, Vt = np.linalg.svd(X @ W, full_matrices=False)
        V = Vt[:r].T                                       # (H, r)
        s.mats["U"], s.mats["V"] = W @ V, V.T
    def predict(s, X, H=None): return (X @ s.mats["U"]) @ s.mats["V"]
    @staticmethod
    def n(L, p, H, r=8, **kw): return min(r, H, L) * (L + H)


CORES = {"linear": LinearCore, "period": PeriodCore, "fourier": FourierCore, "basis": BasisCore}


# ---------------------------------------------------------------- model
def moving_avg(X, k):
    k = k | 1
    pad = k // 2
    Xp = np.concatenate([np.repeat(X[:, :1], pad, 1), X, np.repeat(X[:, -1:], pad, 1)], 1)
    c = np.cumsum(np.pad(Xp, ((0, 0), (1, 0))), axis=1)
    return (c[:, k:] - c[:, :-k]) / k


PRIOR_CORES = ("linear", "period", "fourier")
SRC_SEEDS = (1000, 1001, 1002)           # pre-training corpus: disjoint from every evaluation series


class Model:
    def __init__(s, spec):
        s.sp = spec
        s.p = spec["period"]
        s.L = eff_len(spec["lookback"], s.p)
        kw = dict(k=spec["fourier_k"], r=spec["rank"])
        s.core = CORES[spec["core"]](s.L, s.p, spec["ridge"], **kw)
        s.trend = spec["decomp"] == "ma"
        s.tk = max(1, s.L // spec["trend_stride"]) if s.trend else 0
        s.mats_t = {}

    def _prep(s, X):
        X = X[:, -s.L:]
        m, sc = spec_norm(X, s.sp["norm"])
        return (X - m) / sc, m, sc

    def _base(s, Xn, H):
        if not s.sp["snaive"]:
            return 0.0
        return Xn[:, -s.p:][:, np.arange(H) % s.p]

    def _tfeat(s, Xn):
        T = moving_avg(Xn, s.sp["trend_k"])
        return T[:, np.linspace(0, s.L - 1, s.tk).astype(int)]

    def _fit_once(s, Xn, R, w, prior, a0):
        """One weighted closed-form fit of trend head + core on residual targets R."""
        sw = np.sqrt(w)[:, None]
        if s.trend:
            tf = s._tfeat(Xn)
            s.mats_t["Wt"] = ridge(tf * sw, R * sw, s.sp["ridge"],
                                   prior.mats_t.get("Wt") if prior else None, a0)
            R = R - tf @ s.mats_t["Wt"]
            Xs = Xn - moving_avg(Xn, s.sp["trend_k"])
        else:
            Xs = Xn
        if s.sp["core"] == "linear" and g(s.sp, "adapter") == "lowrank" and prior is not None:
            W0 = prior.core.mats["W"]                           # LoRA analogue: frozen pre-trained W0 + low-rank delta
            R1 = R - Xs @ W0
            Wd = ridge(Xs * sw, R1 * sw, s.sp["ridge"])
            r = min(g(s.sp, "adapter_rank"), Wd.shape[1])
            _, _, Vt = np.linalg.svd(Xs @ Wd, full_matrices=False)
            V = Vt[:r].T
            s.core.mats["W"] = W0 + (Wd @ V) @ V.T
        else:
            s.core.fit(Xs * sw, R * sw, prior.core.mats if prior else None, a0)
        s.H = R.shape[1]
        return Xs

    def fit(s, X, Y, w=None, prior=None):
        Xn, m, sc = s._prep(X)
        H = Y.shape[1]
        R = (Y - m) / sc - s._base(Xn, H)
        w = np.ones(len(Xn)) if w is None else w
        lam = g(s.sp, "distill_lambda")
        if lam > 0:                                             # distillation: student also learns from a full-linear teacher
            sw = np.sqrt(w)[:, None]
            Wt = ridge(Xn * sw, R * sw, s.sp["ridge"])
            R = (1 - lam) * R + lam * (Xn @ Wt)
        a0 = g(s.sp, "prior_alpha") if prior is not None else 0.0
        s._fit_once(Xn, R, w, prior, a0)
        if g(s.sp, "robust"):                                   # IRLS / Huber: down-weight windows the first fit cannot explain
            res = np.abs(R - s._raw_pred(Xn, H) + s._base(Xn, H) * 0).mean(1)
            scale = 1.4826 * np.median(res) + 1e-9
            w2 = w * np.minimum(1.0, 1.5 * scale / (res + 1e-9))
            s._fit_once(Xn, R, w2, prior, a0)
        return s

    def _raw_pred(s, Xn, H):
        if s.trend:
            out = s._tfeat(Xn) @ s.mats_t["Wt"][:, :H]
            Xs = Xn - moving_avg(Xn, s.sp["trend_k"])
        else:
            out, Xs = 0.0, Xn
        return out + s.core.predict(Xs, H)[:, :H]

    def predict(s, X, H=None):
        H = H or s.H
        Xn, m, sc = s._prep(X)
        return (s._base(Xn, H) + s._raw_pred(Xn, H)) * sc + m

    # ---- post-training -------------------------------------------------
    def _all_mats(s):
        return [(s.core.mats, k) for k in s.core.mats] + [(s.mats_t, k) for k in s.mats_t]

    def post_train(s):
        mats = s._all_mats()
        pr = s.sp["prune"]
        if pr > 0:
            allw = np.concatenate([np.abs(d[k]).ravel() for d, k in mats])
            thr = np.quantile(allw, pr)
            for d, k in mats:
                d[k] = np.where(np.abs(d[k]) < thr, 0.0, d[k])
        q = s.sp["quant"]
        if q != "fp32":
            qmax = 127 if q == "int8" else 7
            for d, k in mats:
                w = d[k]
                scale = np.abs(w).max() / qmax or 1.0
                d[k] = np.round(w / scale).clip(-qmax, qmax) * scale
        return s

    def stored_params(s):
        return int(sum(np.count_nonzero(d[k]) for d, k in s._all_mats()))


def spec_norm(X, mode):
    if mode == "none":
        return 0.0, 1.0
    if mode == "last":
        return X[:, -1:], 1.0
    m = X.mean(1, keepdims=True)
    return (m, 1.0) if mode == "mean" else (m, X.std(1, keepdims=True) + 1e-5)


def members(spec):
    n = g(spec, "ens")
    L = spec["lookback"]
    lbs = [L, max(48, L // 2), min(720, 2 * L)][:n]
    return [{**spec, "lookback": lb} for lb in lbs]


def _count_one(spec, H=PARAM_H) -> int:
    p = spec["period"]; L = eff_len(spec["lookback"], p)
    n = CORES[spec["core"]].n(L, p, H, k=spec["fourier_k"], r=spec["rank"])
    if spec["decomp"] == "ma":
        n += max(1, L // spec["trend_stride"]) * H
    if spec["core"] == "linear" and g(spec, "adapter") == "lowrank" and g(spec, "pretrain") in ("synth", "pool"):
        n += min(g(spec, "adapter_rank"), H) * (L + H)          # frozen W0 + low-rank delta are both stored
    return int(round(n * (1.0 - spec["prune"])))


def count_params(spec, H=PARAM_H) -> int:
    return sum(_count_one(m, H) for m in members(spec))


def est_mem_mb(spec) -> float:
    """Peak resident memory (MB) of one evaluation in a fresh process, fitted to measurements at MAX_WINDOW_ELEMS = 4M
    (tests/test_tinyts.py re-measures it and fails if this ever UNDER-predicts). Additive terms, deliberately a little pessimistic:
    interpreter + numpy + the capped window matrices ~560; pre-training +100; augmentation +170; ensemble +130 (members run
    sequentially but memory is not fully returned); robust / distillation +100 each; a long look-back +140; MA decomposition +160;
    spectral core +120 (+60 with pre-training); reduced-rank basis +40.
    This is a RESOURCE CAP in the same sense as the parameter cap: a spec the service cannot afford is infeasible."""
    e = 560.0
    e += 100 if g(spec, "pretrain") in ("synth", "pool") else 0
    e += 170 if g(spec, "augment") else 0
    e += 130 if g(spec, "ens") > 1 else 0
    e += 100 if g(spec, "robust") else 0
    e += 100 if g(spec, "distill_lambda") > 0 else 0
    e += 140 if spec["lookback"] >= 500 else 0
    e += 160 if spec["decomp"] == "ma" else 0
    if spec["core"] == "fourier":
        e += 120 + (60 if g(spec, "pretrain") in ("synth", "pool") else 0)
    elif spec["core"] == "basis":
        e += 40
    return e


def bits(spec) -> int:
    return {"fp32": 32, "int8": 8, "int4": 4}[spec["quant"]]


# ---------------------------------------------------------------- evaluation
def _std(series):
    n_tr = SPLIT[0]
    mu, sd = series[:n_tr].mean(0), series[:n_tr].std(0) + 1e-8
    return (series - mu) / sd


def _prior_model(spec, H, tr, src_seeds=SRC_SEEDS):
    """Pre-training: fit the same architecture on a synthetic source corpus (never an evaluation series).
    The pooled window matrix is thinned to at most MAX_WINDOW_ELEMS/4 elements: pre-training used to cost +460 MB of peak memory."""
    L = eff_len(spec["lookback"], spec["period"])
    per_source = MAX_WINDOW_ELEMS // (4 * len(src_seeds))
    Xs, Ys = [], []
    for sd in src_seeds:
        d = _std(synth(sd))
        if g(spec, "clip"):
            d = np.clip(d, -g(spec, "clip_z"), g(spec, "clip_z"))
        X, Y = windows(d, 0, SPLIT[0], L, H, max(4, tr * 2))
        if X.size + Y.size > per_source:                                  # deterministic thinning
            keep = np.linspace(0, len(X) - 1, max(8, per_source // (L + H))).astype(int)
            X, Y = X[keep], Y[keep]
        Xs.append(X); Ys.append(Y)
    return Model({**spec, "distill_lambda": 0.0, "robust": False}).fit(np.concatenate(Xs), np.concatenate(Ys))


REF_PERIOD = 24                       # seasonal-naive reference period (daily cycle of hourly data)


def _snaive(X, H):
    return X[:, -REF_PERIOD:][:, np.arange(H) % REF_PERIOD]


def evaluate(spec, stage, data_dir=None, split="val", profile="clean", deep=None):
    """Headline: rel_mse = geometric mean over (dataset, horizon) of MSE(model)/MSE(seasonal naive) -- <1 beats naive.
    deep (default: stages s2/final) adds the secondary dimensions: robustness, seasonal fidelity, data-efficiency, decay."""
    from .. import metrics as M
    stages, src_seeds = PROFILES[profile]
    cfg = stages[stage]
    deep = stage in ("s2", "final") if deep is None else deep
    mses, maes, ratios, ratios_mae, fam, per_h = [], [], [], [], [], {}
    sfi, ri_parts, t0 = [], [], time.time()
    rng = np.random.default_rng(12345)
    for key in cfg["seeds"]:
        raw = get_series(key, data_dir)
        n_tr = SPLIT[0] if not data_dir else int(len(raw) * .6)
        n_va = SPLIT[1] if not data_dir else int(len(raw) * .2)
        mu, sd_ = raw[:n_tr].mean(0), raw[:n_tr].std(0) + 1e-8
        data = (raw - mu) / sd_
        cdata = np.clip(data, -g(spec, "clip_z"), g(spec, "clip_z")) if g(spec, "clip") else data
        lo, hi = (n_tr, n_tr + n_va) if split == "val" else (n_tr + n_va, len(data))
        Lmax = max(eff_len(m["lookback"], m["period"]) for m in members(spec))
        for H in cfg["H"]:
            Xv, _ = windows(cdata, lo, hi, Lmax, H, cfg["va"])               # inputs may be clipped (deployable preprocessing)
            _, Yv = windows(data, lo, hi, Lmax, H, cfg["va"])                # targets are never clipped
            fitted = []
            for ms in members(spec):
                L = eff_len(ms["lookback"], ms["period"])
                Xt, Yt, tt = windows(cdata, 0, n_tr, L, H, cfg["tr"], with_t=True)
                w = np.exp(g(ms, "recency") * (tt - 1.0))
                if g(ms, "augment"):
                    Xa = Xt + rng.normal(0, 1, Xt.shape) * g(ms, "aug_sigma") * Xt.std(1, keepdims=True)
                    Xt, Yt, w = np.concatenate([Xt, Xa]), np.concatenate([Yt, Yt]), np.concatenate([w, w])
                prior = None
                if g(ms, "pretrain") == "synth" and ms["core"] in PRIOR_CORES:
                    prior = _prior_model(ms, H, cfg["tr"], src_seeds)
                fitted.append(Model(ms).fit(Xt, Yt, w, prior).post_train())
            pred = np.mean([m.predict(Xv, H) for m in fitted], 0)
            err = pred - Yv
            if not np.isfinite(err).all():
                raise FloatingPointError("non-finite forecast")
            ref = _snaive(Xv, H) - Yv
            mses.append(float((err ** 2).mean())); maes.append(float(np.abs(err).mean()))
            ratios.append(mses[-1] / max(float((ref ** 2).mean()), 1e-12))
            ratios_mae.append(maes[-1] / max(float(np.abs(ref).mean()), 1e-12))
            fam.append(str(data_dir or key) if data_dir else str(key))
            per_h.setdefault(H, []).append(mses[-1])
            if deep:
                if H >= 192:
                    sfi.append(M.seasonal_fidelity(pred, Yv, [24, 168]))
                if H == cfg["H"][0]:
                    prng = np.random.default_rng(777)
                    pm = []
                    for kind in M.PERTURBATIONS:
                        pe = np.mean([m.predict(M.perturb(Xv, kind, prng), H) for m in fitted], 0) - Yv
                        pm.append(float((pe ** 2).mean()))
                    ri_parts.append(M.robustness_index(mses[-1], pm))
    wts = M.family_weights(fam)
    npar = count_params(spec)
    out = {"rel_mse": M.gmre(ratios, wts), "rel_mae": M.gmre(ratios_mae, wts),
           "val_mse": float(np.mean(mses)), "val_mae": float(np.mean(maes)), "n_params": npar,
           "bytes": int(npar * bits(spec) / 8)}
    out.update({f"mse_h{h}": float(np.mean(v)) for h, v in per_h.items()})
    if deep:
        out["robustness_index"] = float(np.mean(ri_parts)) if ri_parts else float("nan")
        if sfi:
            out["seasonal_fidelity"] = float(np.nanmean(sfi))
        out["horizon_decay"] = M.horizon_decay({h: float(np.mean(v)) for h, v in per_h.items()})
    out["fit_s"] = time.time() - t0
    return {k: v for k, v in out.items() if not (isinstance(v, float) and math.isnan(v))}


# ---------------------------------------------------------------- catalog-driven evaluation (real + synthetic-suite data)
CAT_STAGES = {                       # role/dataset choice is PROTOCOL, fixed here + in data/catalog.toml; no agent chooses data
    "s0": dict(names=["etth1"], H=[96], cap_tr=8000, cap_va=4000, folds=1),
    "s1": dict(names=["etth1", "weather", "exchange"], H=None, cap_tr=15000, cap_va=8000, folds=3),
    "s2": dict(names=["etth1", "ettm1", "weather", "exchange"], H=None, cap_tr=25000, cap_va=12000, folds=3),
}
_POOL_CACHE: dict = {}


def rolling_blocks(ser, folds: int):
    """Expanding-window origins over the last 40% of train+val (one block for folds=1 = the plain validation split).
    Fold i trains on targets in [0, b_i) and is evaluated on targets in [b_i, b_i+1). A single fixed validation segment
    is unreliable (ETTh1: validation MSE ~2x test and rankings flip), so selection looks at several origins."""
    end = ser.n_train + ser.n_val
    if folds <= 1:
        return [(ser.n_train, end)]
    start = int(0.6 * end)
    edges = [start + round(i * (end - start) / folds) for i in range(folds + 1)]
    return list(zip(edges[:-1], edges[1:]))


def _pool_prior(spec, L, H, exclude_family, cat, cap=3000):
    cap = max(64, min(cap, MAX_WINDOW_ELEMS // (4 * 3 * (L + H))))      # total pooled elements <= MAX/4 across <= 3 sources
    """Pre-training on OTHER dev-role datasets (never the target's family, never confirm/sealed/stress data)."""
    key = (json.dumps({k: spec[k] for k in sorted(spec) if k not in ("prior_alpha", "adapter", "adapter_rank", "prune", "quant")}, sort_keys=True, default=str),
           L, H, exclude_family)
    if key in _POOL_CACHE:
        return _POOL_CACHE[key]
    from ..data import sample_windows
    Xs, Ys = [], []
    for nm in cat.names("dev"):
        if cat.meta[nm]["family"] == exclude_family:             # decide from catalog metadata: excluded data is never even loaded
            continue
        o = cat.load(nm)
        if o.n_train < L + H + 32:
            continue
        Xo, Yo = sample_windows(o.standardised(), 0, o.n_train, L, H, cap, 5)
        Xs.append(Xo); Ys.append(Yo)
    prior = Model({**spec, "distill_lambda": 0.0, "robust": False}).fit(np.concatenate(Xs), np.concatenate(Ys)) if Xs else None
    if len(_POOL_CACHE) > 64:
        _POOL_CACHE.clear()
    _POOL_CACHE[key] = prior
    return prior
_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        from ..data import Catalog
        from ..data import locate_catalog
        _CATALOG = Catalog(locate_catalog())
    return _CATALOG


def evaluate_catalog(spec, names, split="val", horizons=None, cap_tr=25000, cap_va=12000, deep=False, tag=0, per_dataset=False, folds=1):
    """Headline rel_mse = family-weighted geometric mean of MSE(model)/MSE(seasonal naive at the dataset's own period) over
    (dataset, horizon). Lookback is capped per dataset so short series stay usable; horizons that do not fit are skipped
    and reported. Windows are a deterministic capped sample."""
    from .. import metrics as M
    from ..data import sample_windows
    cat = catalog()
    t0 = time.time()
    ratios, ratios_mae, fam, mses, per_h, per_ds, skipped, sfi, ri_parts = [], [], [], [], {}, {}, [], [], []
    for name in names:
        ser = cat.load(name)
        d = ser.standardised()
        cd = np.clip(d, -g(spec, "clip_z"), g(spec, "clip_z")) if g(spec, "clip") else d
        blocks = rolling_blocks(ser, folds) if split == "val" else [(ser.n_train + ser.n_val, len(d))]
        for H in (horizons or ser.horizons):
          for fi, (lo, hi) in enumerate(blocks):
            tr_end = ser.n_train if split != "val" or folds <= 1 else lo          # fold i trains only on targets before its block
            if hi - lo < H + 8 or tr_end < 2 * H + 16:
                skipped.append(f"{name}:{H}:{fi}"); continue
            preds, Xv, Yv = [], None, None
            members_ = members(spec)
            Lm = [min(eff_len(m["lookback"], m["period"]), max(m["period"], (tr_end - H) // 3)) for m in members_]
            Lmax = max(max(Lm), ser.ref_period)
            fitted = []
            for ms, L in zip(members_, Lm):
                ms = {**ms, "lookback": L}
                Xt, Yt, tt = sample_windows(cd, 0, tr_end, L, H, min(cap_tr, MAX_WINDOW_ELEMS // (L + H)), 11 + tag + fi, with_t=True)
                w = np.exp(g(ms, "recency") * (tt - 1.0))
                if g(ms, "augment"):
                    ar = np.random.default_rng(12345)
                    Xa = Xt + ar.normal(0, 1, Xt.shape) * g(ms, "aug_sigma") * Xt.std(1, keepdims=True)
                    Xt, Yt, w = np.concatenate([Xt, Xa]), np.concatenate([Yt, Yt]), np.concatenate([w, w])
                prior = None
                if g(ms, "pretrain") == "synth" and ms["core"] in PRIOR_CORES:
                    prior = _prior_model({**ms, "lookback": L}, H, 4, PROFILES["hard"][1])
                elif g(ms, "pretrain") == "pool" and ms["core"] in PRIOR_CORES:
                    prior = _pool_prior({**ms, "lookback": L}, L, H, ser.family, cat)
                fitted.append(Model(ms).fit(Xt, Yt, w, prior).post_train())
            cva = min(cap_va, MAX_WINDOW_ELEMS // (Lmax + H))
            Xv, _ = sample_windows(cd, lo, hi, Lmax, H, cva, 21 + tag + fi)
            _, Yv = sample_windows(d, lo, hi, Lmax, H, cva, 21 + tag + fi)
            pred = np.mean([m.predict(Xv, H) for m in fitted], 0)
            err = pred - Yv
            if not np.isfinite(err).all():
                raise FloatingPointError(f"non-finite forecast on {name}")
            rp = min(ser.ref_period, Xv.shape[1])
            ref = Xv[:, -rp:][:, np.arange(H) % rp] - Yv
            mse = float((err ** 2).mean())
            r = mse / max(float((ref ** 2).mean()), 1e-12)
            ratios.append(r); ratios_mae.append(float(np.abs(err).mean()) / max(float(np.abs(ref).mean()), 1e-12))
            fam.append(ser.family); mses.append(mse); per_h.setdefault(H, []).append(mse); per_ds.setdefault(f"{name}@{H}", []).append(r)
            if deep:
                if H >= 192:
                    sfi.append(M.seasonal_fidelity(pred, Yv, [p_ for p_ in (24, 168) if p_ * 1.5 <= H]))
                if H == (horizons or ser.horizons)[0]:
                    prng = np.random.default_rng(777)
                    pm = [float(((np.mean([m.predict(M.perturb(Xv, k, prng), H) for m in fitted], 0) - Yv) ** 2).mean()) for k in M.PERTURBATIONS]
                    ri_parts.append(M.robustness_index(mse, pm))
    if not ratios:
        raise ValueError("no usable (dataset, horizon) pair")
    wts = M.family_weights(fam)
    npar = count_params(spec)
    per_ds = {k: float(np.exp(np.mean(np.log(v)))) for k, v in per_ds.items()}
    out = {"rel_mse": M.gmre(ratios, wts), "rel_mae": M.gmre(ratios_mae, wts), "val_mse": float(np.mean(mses)), "n_params": npar,
           "bytes": int(npar * bits(spec) / 8), "n_tasks": len(ratios), "n_skipped": len(skipped)}
    if deep:
        out["robustness_index"] = float(np.mean(ri_parts)) if ri_parts else float("nan")
        out["seasonal_fidelity"] = float(np.nanmean(sfi)) if sfi else float("nan")
        out["horizon_decay"] = M.horizon_decay({h: float(np.mean(v)) for h, v in per_h.items()})
    if per_dataset:
        out.update({f"rel__{k}": v for k, v in per_ds.items()})
    out["fit_s"] = time.time() - t0
    return {k: v for k, v in out.items() if not (isinstance(v, float) and math.isnan(v))}


def evaluate_stage_catalog(spec, stage, split="val"):
    c = CAT_STAGES[stage]
    return evaluate_catalog(spec, c["names"], split, c["H"], c["cap_tr"], c["cap_va"], deep=(stage == "s2"), folds=c.get("folds", 1))


def baseline_specs():
    base = dict(lookback=336, period=24, norm="revin", core="period", rank=8, fourier_k=16, decomp="none",
                trend_k=25, trend_stride=24, snaive=False, ridge=1.0, prune=0.0, quant="fp32", **LEVER_DEFAULTS)
    return {"period_linear": base,
            "dlinear_like": {**base, "core": "linear", "period": 1, "decomp": "ma", "trend_stride": 1, "norm": "last"},
            "fits_like": {**base, "core": "fourier", "period": 1, "fourier_k": 24, "norm": "mean"},
            "seasonal_naive": {**base, "core": "period", "snaive": True, "ridge": 1e9}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="s0"); ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--attempt", type=int, default=1); ap.add_argument("--final", action="store_true")
    ap.add_argument("--data", default=None); ap.add_argument("--profile", default="clean")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())
    if a.profile == "catalog":
        if a.final:                                   # one-shot: TEST windows of every real dataset (dev + confirm + sealed families)
            cat = catalog()
            names = [n for n in cat.names() if cat.meta[n]["role"] in ("dev", "confirm", "sealed") and not n.startswith("syn_")]
            m = evaluate_catalog(spec, names, "test", None, 40000, 20000, deep=True, per_dataset=True)
        else:
            m = evaluate_stage_catalog(spec, a.stage)
    else:
        m = evaluate(spec, "final" if a.final else a.stage, a.data, "test" if a.final else "val", a.profile)
    Path(a.out, "metrics.json").write_text(json.dumps(m))


if __name__ == "__main__":
    main()
