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
from numpy.lib.stride_tricks import sliding_window_view

PARAM_H = 720
MAX_WINDOW_ELEMS = int(__import__('os').environ.get('ANTS_MAX_WINDOW_ELEMS', 4_000_000))      # elements per window matrix (X and Y together; float32 -> 16 MB). Evaluation memory is part of the
                                  # cost model: the 2 GB control tower runs with MemoryMax=950M, so a spec must not be able to ask for more.
CHUNK_ELEMS = 1 << 18             # float64 working set of one streaming step (2 MB): fits never hold a full-size float64 copy of the windows
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


class Scratch:
    """One reusable byte buffer carved into C-contiguous arrays: take() hands out views and invalidates the previous ones.
    Window matrices are the biggest allocations of an evaluation; reusing one buffer across horizons and members keeps
    the allocator from fragmenting and the resident peak flat (pages of a larger-than-needed buffer are never touched)."""
    def __init__(s): s.buf = np.empty(0, np.uint8)

    def take(s, dtype, *shapes):
        dt = np.dtype(dtype)
        sizes = [-(-int(np.prod(sh)) * dt.itemsize // 64) * 64 for sh in shapes]
        if s.buf.size < sum(sizes):
            s.buf = np.empty(sum(sizes), np.uint8)
        out, o = [], 0
        for sh, nb in zip(shapes, sizes):
            out.append(s.buf[o:o + nb].view(dt)[:int(np.prod(sh))].reshape(sh)); o += nb
        return out


def _plan(data, lo, hi, L, H, stride):
    """(window start positions after the element cap, number of windows over all channels)."""
    n_w = max(1, (hi - max(lo, L) - H + 1) // max(1, stride)) * data.shape[1]
    if n_w * (L + H) > MAX_WINDOW_ELEMS:                          # deterministic thinning instead of an unbounded matrix
        stride = int(np.ceil(stride * n_w * (L + H) / MAX_WINDOW_ELEMS))
    starts = np.arange(max(lo, L), hi - H + 1, stride)
    return starts, len(starts) * data.shape[1]


def windows(data, lo, hi, L, H, stride, with_t=False, want="xy", rows=None, scratch=None):
    """All channel-independent (input, target) windows whose TARGET lies in [lo, hi), in the dtype of `data` (float32 halves the
    memory). want='x' / 'y' builds only that half (the other is None). rows = indices into the channel-major enumeration of the
    windows: build only those. scratch = a Scratch to build into. Rows are written straight into the output: no (n, L+H) intermediate."""
    starts, total = _plan(data, lo, hi, L, H, stride)
    ns = max(1, len(starts))
    sel = np.arange(total) if rows is None else np.asarray(rows)
    ch, ki = sel // ns, sel % ns
    shapes = [(len(sel), L)] * ("x" in want) + [(len(sel), H)] * ("y" in want)
    mats = scratch.take(data.dtype, *shapes) if scratch else [np.empty(sh, data.dtype) for sh in shapes]
    X, Y = (mats.pop(0) if "x" in want else None), (mats.pop(0) if "y" in want else None)
    step = max(1, CHUNK_ELEMS // (L + H))
    for c in np.unique(ch):
        pos = np.flatnonzero(ch == c)
        col = np.ascontiguousarray(data[:, c])
        sx, sy = sliding_window_view(col, L), sliding_window_view(col, H)
        for a in range(0, len(pos), step):
            p = pos[a:a + step]
            st = starts[ki[p]]
            if X is not None:
                X[p] = sx[st - L]
            if Y is not None:
                Y[p] = sy[st]
    out = (X, Y)
    return out + ((starts[ki] - lo) / max(1, hi - lo),) if with_t else out


# ---------------------------------------------------------------- cores
def ridge_gram(A, rhs, alpha, W0=None, alpha0=0.0):
    """min ||Y - XW||^2 + alpha*||W||^2 + alpha0*||W - W0||^2  from the Gram matrices A = X^T X, rhs = X^T Y
    (alpha0/W0 = fine-tune towards a pre-trained prior). A is not modified."""
    sc = max(1.0, np.trace(A) / A.shape[0])
    A = A.copy()
    A[np.diag_indices_from(A)] += (alpha + alpha0) * sc
    if W0 is not None and alpha0 > 0:
        rhs = rhs + alpha0 * sc * W0
    return np.linalg.solve(A, rhs)


def ridge(X, Y, alpha, W0=None, alpha0=0.0):
    return ridge_gram(X.T @ X, X.T @ Y, alpha, W0, alpha0)


class Gram:
    """Streaming normal equations: A = sum F^T F and B = sum F^T T over float64 row chunks (products land in reused buffers)."""
    def __init__(s, d, t):
        s.A, s.B, s._a, s._b = np.zeros((d, d)), np.zeros((d, t)), np.empty((d, d)), np.empty((d, t))
    def add(s, F, T):
        s.A += np.matmul(F.T, F, out=s._a)
        s.B += np.matmul(F.T, T, out=s._b)


def top_directions(W, G, r):
    """Top-r right singular vectors (H, r) of X @ W, from the (H, H) matrix W^T G W with G = X^T X: no (n, H) matrix, no SVD of it."""
    return np.linalg.eigh(W.T @ G @ W)[1][:, ::-1][:, :r]


def eff_len(L, p):
    return max(p, (L // p) * p)


class Core:
    """A core is fitted by streaming: start(H) -> state, add(state, X, Y) per row chunk, finish(state, prior, a0)."""
    mats: dict

    def count(self, H):                                   # analytic, unpruned
        raise NotImplementedError

    def fit(s, X, Y, prior=None, a0=0.0):                 # one-shot convenience
        st = s.start(Y.shape[1]); s.add(st, X, Y); s.finish(st, prior, a0)


class LinearCore(Core):
    def __init__(s, L, p, alpha, **kw): s.L, s.alpha, s.mats = L, alpha, {}
    def start(s, H): return Gram(s.L, H)
    def add(s, st, X, Y): st.add(X, Y)
    def finish(s, st, prior=None, a0=0.0):
        s.mats["W"] = ridge_gram(st.A, st.B, s.alpha, prior["W"] if prior else None, a0)
    def predict(s, X, H=None): return X @ s.mats["W"]
    @staticmethod
    def n(L, p, H, **kw): return L * H


class PeriodCore(Core):
    def __init__(s, L, p, alpha, **kw): s.L, s.p, s.alpha, s.mats = L, p, alpha, {}
    def start(s, H):
        m, full = s.L // s.p, H // s.p
        return dict(H=H, full=Gram(m, full) if full else None, rem=Gram(m, 1) if H % s.p else None)
    def add(s, st, X, Y):
        p, n, full = s.p, len(X), st["H"] // s.p
        Z = X.reshape(n, s.L // p, p).transpose(0, 2, 1)             # (n, phase, m): phase j holds X[:, j::p]
        if full:                                                      # one shared map: every phase is a row of the same regression
            st["full"].add(Z.reshape(n * p, -1), Y[:, :full * p].reshape(n, full, p).transpose(0, 2, 1).reshape(n * p, full))
        if st["rem"]:                                                 # the last, partial period: only the phases that still exist
            rem = st["H"] - full * p
            st["rem"].add(Z[:, :rem].reshape(n * rem, -1), Y[:, full * p:].reshape(n * rem, 1))
    def finish(s, st, prior=None, a0=0.0):
        p, H = s.p, st["H"]
        A, A0, full = np.zeros((-(-H // p), s.L // p)), prior["A"] if prior else None, H // p
        if full:
            A[:full] = ridge_gram(st["full"].A, st["full"].B, s.alpha, None if A0 is None else A0[:full].T, a0).T
        if st["rem"]:
            A[full] = ridge_gram(st["rem"].A, st["rem"].B, s.alpha, None if A0 is None else A0[full][:, None], a0)[:, 0]
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
    def start(s, H):
        s.H = H
        return Gram(2 * s.K, 2 * s._kout(H))
    def add(s, st, X, Y):
        S = np.fft.rfft(np.concatenate([X, Y], 1), axis=1)[:, : s._kout(Y.shape[1])]
        st.add(s._feat(X), np.concatenate([S.real, S.imag], 1))
    def finish(s, st, prior=None, a0=0.0):
        s.mats["W"] = ridge_gram(st.A, st.B, s.alpha, prior["W"] if prior else None, a0)
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
    def start(s, H): return Gram(s.L, H)
    def add(s, st, X, Y): st.add(X, Y)
    def finish(s, st, prior=None, a0=0.0):
        W = ridge_gram(st.A, st.B, s.alpha)
        V = top_directions(W, st.A, min(s.r, W.shape[1], s.L))        # (H, r)
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


class _Rows:
    """Chunked row source of one fit: the windows (X, Y) and, with augmentation, a noisy copy of X after them.
    The noise is drawn from the caller's RNG on the first pass and replayed from the RNG state saved here on every later pass, so
    all passes see the same copy and the RNG ends exactly where one big `rng.normal(size=X.shape)` would have left it:
    no augmented matrix is ever materialised."""
    def __init__(s, X, Y, aug=None):
        s.X, s.Y, s.n, s.aug = X, Y, len(X), aug
        s.nt = s.n * (2 if aug else 1)
        s.step = max(32, CHUNK_ELEMS // (X.shape[1] + Y.shape[1]))
        s.state = aug[0].bit_generator.state if aug else None
        s.live = True

    def __iter__(s):
        for i0 in range(0, s.n, s.step):
            i1 = min(i0 + s.step, s.n)
            yield i0, i1, s.X[i0:i1], s.Y[i0:i1]
        if s.aug:
            gen = s.aug[0]
            if not s.live:
                gen = np.random.Generator(type(gen.bit_generator)())
                gen.bit_generator.state = s.state
            s.live = False
            for i0 in range(0, s.n, s.step):
                i1 = min(i0 + s.step, s.n)
                Xc = np.array(s.X[i0:i1], dtype=np.float64)
                noise = gen.normal(0.0, 1.0, Xc.shape)
                noise *= s.aug[1]; noise *= Xc.std(1, keepdims=True)
                yield s.n + i0, s.n + i1, Xc + noise, s.Y[i0:i1]


def _scaled(A, sw):
    return A if sw is None else A * sw


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
        """Normalised float64 copy of the last L columns of a (chunk of) windows of any dtype, plus the (mean, scale) to undo it."""
        Xn, mode = np.array(X[:, -s.L:], dtype=np.float64), s.sp["norm"]
        if mode == "none":
            return Xn, 0.0, 1.0
        m = Xn[:, -1:].copy() if mode == "last" else Xn.mean(1, keepdims=True)
        Xn -= m
        if mode in ("last", "mean"):
            return Xn, m, 1.0
        sc = np.sqrt(np.einsum("ij,ij->i", Xn, Xn) / Xn.shape[1])[:, None] + 1e-5          # == X.std(1) + 1e-5
        Xn /= sc
        return Xn, m, sc

    def _base(s, Xn, H):
        if not s.sp["snaive"]:
            return 0.0
        return Xn[:, -s.p:][:, np.arange(H) % s.p]

    def _split(s, Xn):
        """(trend features, detrended input) from ONE moving average."""
        ma = moving_avg(Xn, s.sp["trend_k"])
        return ma[:, np.linspace(0, s.L - 1, s.tk).astype(int)], Xn - ma

    def _stream(s, rows, w, Wte=None, lam=0.0):
        """float64 chunks (i0, i1, Xn, R, sqrt-weights): normalised inputs and base-removed (and, with a teacher, distilled) targets."""
        H = rows.Y.shape[1]
        for i0, i1, Xc, Yc in rows:
            Xn, m, sc = s._prep(Xc)
            R = np.array(Yc, dtype=np.float64)
            R -= m; R /= sc
            if s.sp["snaive"]:
                R -= s._base(Xn, H)
            if Wte is not None:
                R *= 1.0 - lam; R += lam * (Xn @ Wte)
            yield i0, i1, Xn, R, None if w is None else np.sqrt(w[i0:i1])[:, None]

    def _fit_once(s, rows, w, Wte, lam, prior, a0):
        """One weighted closed-form fit of trend head + core on the residual targets, streamed over row chunks."""
        H, rd = rows.Y.shape[1], s.sp["ridge"]
        if s.trend:
            gr = Gram(s.tk, H)
            for _, _, Xn, R, sw in s._stream(rows, w, Wte, lam):
                gr.add(_scaled(s._split(Xn)[0], sw), _scaled(R, sw))
            s.mats_t["Wt"] = ridge_gram(gr.A, gr.B, rd, prior.mats_t.get("Wt") if prior else None, a0)
        lora = s.sp["core"] == "linear" and g(s.sp, "adapter") == "lowrank" and prior is not None
        if lora:                                                    # LoRA analogue: frozen pre-trained W0 + low-rank delta
            W0, gr, Gu = prior.core.mats["W"], Gram(s.L, H), np.zeros((s.L, s.L))
        else:
            st = s.core.start(H)
        for _, _, Xn, R, sw in s._stream(rows, w, Wte, lam):
            if s.trend:
                tf, Xs = s._split(Xn)
                R -= tf @ s.mats_t["Wt"]
            else:
                Xs = Xn
            if lora:
                gr.add(_scaled(Xs, sw), _scaled(R - Xs @ W0, sw))
                if sw is not None:
                    Gu += Xs.T @ Xs                                  # the subspace is chosen on the UNweighted inputs
            else:
                s.core.add(st, _scaled(Xs, sw), _scaled(R, sw))
        if lora:
            Wd = ridge_gram(gr.A, gr.B, rd)
            V = top_directions(Wd, gr.A if w is None else Gu, min(g(s.sp, "adapter_rank"), Wd.shape[1]))
            s.core.mats["W"] = W0 + (Wd @ V) @ V.T
        else:
            s.core.finish(st, prior.core.mats if prior else None, a0)
        s.H = H

    def fit(s, X, Y, w=None, prior=None, aug=None):
        """Closed-form fit, streamed in row chunks: X, Y may be float32 (only chunk-sized float64 buffers exist).
        aug = (rng, sigma) appends a copy of the windows with X + N(0, sigma * std(window)) noise and the same targets/weights."""
        rows, H = _Rows(X, Y, aug), Y.shape[1]
        if aug and w is not None:
            w = np.concatenate([w, w])
        lam, Wte = g(s.sp, "distill_lambda"), None
        if lam > 0:                                             # distillation: student also learns from a full-linear teacher
            gr = Gram(s.L, H)
            for _, _, Xn, R, sw in s._stream(rows, w):
                gr.add(_scaled(Xn, sw), _scaled(R, sw))
            Wte = ridge_gram(gr.A, gr.B, s.sp["ridge"])
        a0 = g(s.sp, "prior_alpha") if prior is not None else 0.0
        s._fit_once(rows, w, Wte, lam, prior, a0)
        if g(s.sp, "robust"):                                   # IRLS / Huber: down-weight windows the first fit cannot explain
            res = np.empty(rows.nt)
            for i0, i1, Xn, R, _ in s._stream(rows, None, Wte, lam):
                res[i0:i1] = np.abs(R - s._raw_pred(Xn, H)).mean(1)
            scale = 1.4826 * np.median(res) + 1e-9
            w2 = (np.ones(rows.nt) if w is None else w) * np.minimum(1.0, 1.5 * scale / (res + 1e-9))
            s._fit_once(rows, w2, Wte, lam, prior, a0)
        return s

    def _raw_pred(s, Xn, H):
        if s.trend:
            tf, Xs = s._split(Xn)
            out = tf @ s.mats_t["Wt"][:, :H]
        else:
            out, Xs = 0.0, Xn
        return out + s.core.predict(Xs, H)[:, :H]

    def predict(s, X, H=None):
        H = H or s.H
        out = np.empty((len(X), H))
        step = max(32, CHUNK_ELEMS // (s.L + H))
        for i0 in range(0, len(X), step):
            Xn, m, sc = s._prep(X[i0:i0 + step])
            out[i0:i0 + step] = (s._base(Xn, H) + s._raw_pred(Xn, H)) * sc + m
        return out

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
        """Charged size of the stored weights (same accounting as count_params, at the fitted horizon)."""
        return charged_params(sum(d[k].size for d, k in s._all_mats()), s.sp["prune"], bits(s.sp))


def members(spec):
    n = g(spec, "ens")
    L = spec["lookback"]
    lbs = [L, max(48, L // 2), min(720, 2 * L)][:n]
    return [{**spec, "lookback": lb} for lb in lbs]


def charged_params(n, prune, value_bits) -> int:
    """Size (in weight-equivalents) of n weights of which a fraction `prune` is zeroed. Zeros are only free if their positions are
    stored too: sparse storage costs value + index (ceil(log2 n) bits) per nonzero, so pruning pays only below dense size."""
    nz = int(round(n * (1.0 - prune)))
    if nz >= n:
        return int(n)
    return int(min(n, round(nz * (1 + max(1, math.ceil(math.log2(n))) / value_bits))))


def _count_one(spec, H=PARAM_H) -> int:
    p = spec["period"]; L = eff_len(spec["lookback"], p)
    n = CORES[spec["core"]].n(L, p, H, k=spec["fourier_k"], r=spec["rank"])
    if spec["decomp"] == "ma":
        n += max(1, L // spec["trend_stride"]) * H
    if spec["core"] == "linear" and g(spec, "adapter") == "lowrank" and g(spec, "pretrain") in ("synth", "pool"):
        n += min(g(spec, "adapter_rank"), H) * (L + H)          # frozen W0 + low-rank delta are both stored
    return charged_params(n, spec["prune"], bits(spec))


def count_params(spec, H=PARAM_H) -> int:
    return sum(_count_one(m, H) for m in members(spec))


def est_mem_mb(spec) -> float:
    """Peak resident memory (MB) of one evaluation in a fresh process: the upper envelope (LP fit, then +10%) of 56 random specs x
    {synthetic s1, catalog s1 protocol} measured in fresh processes (max of 2), MAX_WINDOW_ELEMS = 4M, with 1 of 16 held-out specs
    under the fit before the margin. tests/test_tinyts.py re-measures a fixed set and fails if this ever UNDER-predicts.
    Additive terms: interpreter + numpy + capped float32 windows + catalog window sampling 365; pre-training +30; augmentation +27;
    distillation +22; MA trend +8; spectral core +68; reduced-rank basis +78; LoRA adapter +130; a member look-back >= 500 +45;
    and for the dense L x H Gram cores (linear, basis): +55 if look-back >= 500, +50 per extra ensemble member, +86 with MA, +23 with
    robust. The catalog path (data.sample_windows builds an int64 index matrix) is the binding case; the synthetic path peaks ~100-150 MB
    lower. Measured on macOS/Accelerate, where freed LAPACK buffers stay resident: a Linux/OpenBLAS process is expected to be at or below this.
    This is a RESOURCE CAP in the same sense as the parameter cap: a spec the service cannot afford is infeasible."""
    dense = spec["core"] in ("linear", "basis")
    big = max(m["lookback"] for m in members(spec)) >= 500
    e = 365.0
    e += 30 if g(spec, "pretrain") in ("synth", "pool") else 0
    e += 27 if g(spec, "augment") else 0
    e += 22 if g(spec, "distill_lambda") > 0 else 0
    e += 8 if spec["decomp"] == "ma" else 0
    e += {"fourier": 68, "basis": 78}.get(spec["core"], 0)
    e += 130 if spec["core"] == "linear" and g(spec, "adapter") == "lowrank" else 0
    e += 45 if big else 0
    if dense:
        e += 55 if big else 0
        e += 50 * (g(spec, "ens") - 1)
        e += 86 if spec["decomp"] == "ma" else 0
        e += 23 if g(spec, "robust") else 0
    return e


def bits(spec) -> int:
    return {"fp32": 32, "int8": 8, "int4": 4}[spec["quant"]]


# ---------------------------------------------------------------- evaluation
def _std(series):
    n_tr = SPLIT[0]
    mu, sd = series[:n_tr].mean(0), series[:n_tr].std(0) + 1e-8
    return (series - mu) / sd


_PRIOR_KEYS = ("lookback", "period", "norm", "core", "rank", "fourier_k", "decomp", "trend_k", "trend_stride", "snaive", "ridge", "clip", "clip_z")


def _prior_model(spec, H, tr, src_seeds=SRC_SEEDS, cache=None):
    """Pre-training: fit the same architecture on a synthetic source corpus (never an evaluation series).
    The pooled window matrix is thinned to at most MAX_WINDOW_ELEMS/4 elements: pre-training used to cost +460 MB of peak memory.
    The prior does not depend on the evaluation series, so `cache` (a dict owned by the caller) reuses it across series and folds."""
    L = eff_len(spec["lookback"], spec["period"])
    key = (tuple(g(spec, k) if k in LEVER_DEFAULTS else spec[k] for k in _PRIOR_KEYS), H, tr, tuple(src_seeds))
    if cache is not None and key in cache:
        return cache[key]
    per_source = MAX_WINDOW_ELEMS // (4 * len(src_seeds))
    Xs, Ys = [], []
    for sd in src_seeds:
        d = _std(synth(sd))
        if g(spec, "clip"):
            d = np.clip(d, -g(spec, "clip_z"), g(spec, "clip_z"))
        d = d.astype(np.float32)
        stride = max(4, tr * 2)
        total = _plan(d, 0, SPLIT[0], L, H, stride)[1]
        rows = None
        if total * (L + H) > per_source:                                  # deterministic thinning, built row by row
            rows = np.linspace(0, total - 1, max(8, per_source // (L + H))).astype(int)
        X, Y = windows(d, 0, SPLIT[0], L, H, stride, rows=rows)
        Xs.append(X); Ys.append(Y)
    prior = Model({**spec, "distill_lambda": 0.0, "robust": False}).fit(np.concatenate(Xs), np.concatenate(Ys))
    if cache is not None:
        cache[key] = prior
    return prior


REF_PERIOD = 24                       # seasonal-naive reference period (daily cycle of hourly data)


def _score(fitted, Xv, Yv, H, rp, keep=False, Xref=None):
    """Ensemble forecast scored against Yv and against seasonal naive (period rp) of Xref (default Xv: pass the UNCLIPPED inputs when
    Xv is clipped, so the denominator of rel_mse does not depend on the clipping lever), streamed over row chunks so that no
    (n, H) forecast, error or reference matrix exists (keep=True also returns the forecast).
    Returns (mse, mae, ref_mse, ref_mae, pred or None)."""
    n, step, idx = len(Xv), max(32, CHUNK_ELEMS // H), np.arange(H) % rp
    Xref = Xv if Xref is None else Xref
    pred = np.empty((n, H)) if keep else None
    acc = np.zeros(4)
    for i0 in range(0, n, step):
        sl = slice(i0, i0 + step)
        p = fitted[0].predict(Xv[sl], H)
        for m in fitted[1:]:
            p += m.predict(Xv[sl], H)
        p /= len(fitted)
        if keep:
            pred[sl] = p
        e = np.subtract(p, Yv[sl], out=p)
        if not np.isfinite(e).all():
            raise FloatingPointError("non-finite forecast")
        r = np.subtract(Xref[sl][:, -rp:][:, idx], Yv[sl], dtype=np.float64)
        acc += (np.vdot(e, e), np.abs(e, out=e).sum(), np.vdot(r, r), np.abs(r, out=r).sum())
    return (*(acc / max(1, n * H)), pred)


def evaluate(spec, stage, data_dir=None, split="val", profile="clean", deep=None):
    """Headline: rel_mse = geometric mean over (dataset, horizon) of MSE(model)/MSE(seasonal naive) -- <1 beats naive.
    deep (default: stages s2/final) adds the secondary dimensions: robustness, seasonal fidelity, data-efficiency, decay.
    Windows are float32 (statistics are accumulated in float64 inside the fits) and freed as soon as a member / horizon is done."""
    from .. import metrics as M
    stages, src_seeds = PROFILES[profile]
    cfg = stages[stage]
    deep = stage in ("s2", "final") if deep is None else deep
    mses, maes, ratios, ratios_mae, fam, per_h = [], [], [], [], [], {}
    sfi, ri_parts, t0 = [], [], time.time()
    rng = np.random.default_rng(12345)
    priors, train_buf, val_buf, clip_buf = {}, Scratch(), Scratch(), Scratch()
    for key in cfg["seeds"]:
        raw = get_series(key, data_dir)
        n_tr = SPLIT[0] if not data_dir else int(len(raw) * .6)
        n_va = SPLIT[1] if not data_dir else int(len(raw) * .2)
        mu, sd_ = raw[:n_tr].mean(0), raw[:n_tr].std(0) + 1e-8
        data = (raw - mu) / sd_
        clip = g(spec, "clip")
        cdata = np.clip(data, -g(spec, "clip_z"), g(spec, "clip_z")) if clip else data
        data32 = data.astype(np.float32)
        cdata32 = cdata.astype(np.float32) if clip else data32
        del data, cdata
        lo, hi = (n_tr, n_tr + n_va) if split == "val" else (n_tr + n_va, len(data32))
        Lmax = max(eff_len(m["lookback"], m["period"]) for m in members(spec))
        for H in cfg["H"]:
            Xraw, Yv = windows(data32, lo, hi, Lmax, H, cfg["va"], scratch=val_buf)       # targets and the naive reference are never clipped
            Xv = windows(cdata32, lo, hi, Lmax, H, cfg["va"], want="x", scratch=clip_buf)[0] if clip else Xraw   # model inputs may be clipped
            fitted = []
            for ms in members(spec):
                L = eff_len(ms["lookback"], ms["period"])
                Xt, Yt, tt = windows(cdata32, 0, n_tr, L, H, cfg["tr"], with_t=True, scratch=train_buf)
                w = np.exp(g(ms, "recency") * (tt - 1.0)) if g(ms, "recency") else None
                aug = (rng, g(ms, "aug_sigma")) if g(ms, "augment") else None
                prior = None
                if g(ms, "pretrain") == "synth" and ms["core"] in PRIOR_CORES:
                    prior = _prior_model(ms, H, cfg["tr"], src_seeds, priors)
                fitted.append(Model(ms).fit(Xt, Yt, w, prior, aug).post_train())
            mse, mae, ref_mse, ref_mae, pred = _score(fitted, Xv, Yv, H, REF_PERIOD, keep=deep, Xref=Xraw)
            mses.append(mse); maes.append(mae)
            ratios.append(mse / max(ref_mse, 1e-12))
            ratios_mae.append(mae / max(ref_mae, 1e-12))
            fam.append(str(data_dir or key) if data_dir else str(key))
            per_h.setdefault(H, []).append(mses[-1])
            if deep:
                if H >= 192:
                    sfi.append(M.seasonal_fidelity(pred, Yv, [24, 168]))
                if H == cfg["H"][0]:
                    prng = np.random.default_rng(777)
                    X64, pm = Xv.astype(np.float64), []
                    for kind in M.PERTURBATIONS:
                        pe = np.mean([m.predict(M.perturb(X64, kind, prng), H) for m in fitted], 0) - Yv
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
        Xo, Yo = sample_windows(o.standardised().astype(np.float32), 0, o.n_train, L, H, cap, 5)
        Xs.append(Xo); Ys.append(Yo)
    prior = Model({**spec, "distill_lambda": 0.0, "robust": False}).fit(np.concatenate(Xs), np.concatenate(Ys)) if Xs else None
    del Xs, Ys
    if sum(sum(v.nbytes for v in m.core.mats.values()) for m in _POOL_CACHE.values() if m) > 64 << 20:      # bounded by bytes, not entries
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
    and reported. Windows are a deterministic capped sample (float32; the fits accumulate in float64)."""
    from .. import metrics as M
    from ..data import sample_windows
    cat = catalog()
    t0 = time.time()
    ratios, ratios_mae, fam, mses, per_h, per_ds, skipped, sfi, ri_parts = [], [], [], [], {}, {}, [], [], []
    priors = {}
    for name in names:
        ser = cat.load(name)
        d = ser.standardised()
        clip = g(spec, "clip")
        cd = np.clip(d, -g(spec, "clip_z"), g(spec, "clip_z")) if clip else d
        d32 = d.astype(np.float32)
        cd32 = cd.astype(np.float32) if clip else d32
        n_d = len(d)
        del d, cd
        blocks = rolling_blocks(ser, folds) if split == "val" else [(ser.n_train + ser.n_val, n_d)]
        for H in (horizons or ser.horizons):
          for fi, (lo, hi) in enumerate(blocks):
            tr_end = ser.n_train if split != "val" or folds <= 1 else lo          # fold i trains only on targets before its block
            if hi - lo < H + 8 or tr_end < 2 * H + 16:
                skipped.append(f"{name}:{H}:{fi}"); continue
            members_ = members(spec)
            Lm = [min(eff_len(m["lookback"], m["period"]), max(m["period"], (tr_end - H) // 3)) for m in members_]
            Lmax = max(max(Lm), ser.ref_period)
            fitted = []
            for ms, L in zip(members_, Lm):
                ms = {**ms, "lookback": L}
                Xt, Yt, tt = sample_windows(cd32, 0, tr_end, L, H, min(cap_tr, MAX_WINDOW_ELEMS // (L + H)), 11 + tag + fi, with_t=True)
                w = np.exp(g(ms, "recency") * (tt - 1.0)) if g(ms, "recency") else None
                aug = (np.random.default_rng(12345), g(ms, "aug_sigma")) if g(ms, "augment") else None
                prior = None
                if g(ms, "pretrain") == "synth" and ms["core"] in PRIOR_CORES:
                    prior = _prior_model({**ms, "lookback": L}, H, 4, PROFILES["hard"][1], priors)
                elif g(ms, "pretrain") == "pool" and ms["core"] in PRIOR_CORES:
                    prior = _pool_prior({**ms, "lookback": L}, L, H, ser.family, cat)
                fitted.append(Model(ms).fit(Xt, Yt, w, prior, aug).post_train())
                del Xt, Yt, tt, w                                                   # a member's windows are dead once it is fitted
            cva = min(cap_va, MAX_WINDOW_ELEMS // (Lmax + H))
            Xv, Yv = sample_windows(cd32, lo, hi, Lmax, H, cva, 21 + tag + fi)
            Xraw = Xv
            if clip:                                                                # targets and the naive reference are never clipped
                Xraw, Yv = sample_windows(d32, lo, hi, Lmax, H, cva, 21 + tag + fi)
            mse, mae, ref_mse, ref_mae, pred = _score(fitted, Xv, Yv, H, min(ser.ref_period, Xv.shape[1]), keep=deep, Xref=Xraw)
            r = mse / max(ref_mse, 1e-12)
            ratios.append(r); ratios_mae.append(mae / max(ref_mae, 1e-12))
            fam.append(ser.family); mses.append(mse); per_h.setdefault(H, []).append(mse); per_ds.setdefault(f"{name}@{H}", []).append(r)
            if deep:
                if H >= 192:
                    sfi.append(M.seasonal_fidelity(pred, Yv, [p_ for p_ in (24, 168) if p_ * 1.5 <= H]))
                if H == (horizons or ser.horizons)[0]:
                    prng = np.random.default_rng(777)
                    X64 = Xv.astype(np.float64)
                    pm = [float(((np.mean([m.predict(M.perturb(X64, k, prng), H) for m in fitted], 0) - Yv) ** 2).mean()) for k in M.PERTURBATIONS]
                    ri_parts.append(M.robustness_index(mse, pm))
            del Xv, Xraw, Yv, pred, fitted
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
