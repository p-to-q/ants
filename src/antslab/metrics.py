"""Evaluation metrics and decision statistics (numpy only, no I/O).

Design rules (see docs/evaluation/E1-quality.md):
  * scale-free: every headline number is a ratio to a reference forecaster (seasonal naive), aggregated GEOMETRICALLY
    (arithmetic means of z-scored MSE let one hard dataset dominate; geometric means treat +10% and -10% symmetrically);
  * correlated datasets are not independent: weights are per SOURCE FAMILY;
  * hard constraints are gates, never terms in a weighted sum; secondary metrics are Pareto dimensions;
  * decisions rest on paired, dependence-aware statistics (block bootstrap, Diebold-Mariano), not on point estimates.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np


# ---------------------------------------------------------------- aggregation
RATIO_FLOOR, RATIO_CAP = 1e-4, 1e4        # a ratio outside this band says "degenerate reference or perfect fit", not "skill"


def gmre(ratios, weights=None, floor: float = RATIO_FLOOR, cap: float = RATIO_CAP) -> float:
    """Weighted geometric mean of model/reference error ratios (<1 beats the reference).
    Fails closed on inputs that would silently manufacture a headline: empty, NaN or negative ratios, negative / NaN /
    all-zero weights raise ValueError. A ratio of 0 (perfect fit) or +inf / huge (zero-error reference) is clipped to
    [floor, cap], so ONE degenerate task can move the geometric mean by at most floor**(w/sum w), never to 0 or inf."""
    r = np.asarray(ratios, float).ravel()
    if r.size == 0:
        raise ValueError("gmre: no ratios")
    if np.isnan(r).any() or (r < 0).any():
        raise ValueError("gmre: ratios must be non-negative numbers (NaN / negative refused)")
    w = np.ones_like(r) if weights is None else np.asarray(weights, float).ravel()
    if w.shape != r.shape or not np.isfinite(w).all() or (w < 0).any() or w.sum() <= 0:
        raise ValueError("gmre: weights must be finite, non-negative, same length as ratios, and not all zero")
    return float(np.exp(np.sum(w * np.log(np.clip(r, floor, cap))) / np.sum(w)))


def family_weights(families: list[str]) -> list[float]:
    """Each independent source family counts once, however many correlated datasets/horizons it contains."""
    n = {f: families.count(f) for f in set(families)}
    return [1.0 / n[f] for f in families]


# ---------------------------------------------------------------- sequence-shape metrics
def seasonal_fidelity(pred: np.ndarray, true: np.ndarray, periods: list[int]) -> float:
    """Does the forecast keep the rhythm? Fit sin/cos at each period over every horizon window by least squares and
    compare amplitude and phase of prediction vs truth. 0 = perfect, ~1 = amplitude or phase badly wrong.
    Invariant to the noise that dominates MSE, so it separates 'learned the season' from 'regressed to the mean'."""
    H = pred.shape[1]
    t = np.arange(H)
    score, n = 0.0, 0
    for p in periods:
        if p * 1.5 > H:
            continue
        B = np.stack([np.sin(2 * np.pi * t / p), np.cos(2 * np.pi * t / p), np.ones(H)], 1)
        P = np.linalg.lstsq(B, pred.T, rcond=None)[0]
        T = np.linalg.lstsq(B, true.T, rcond=None)[0]
        ap, at = np.hypot(P[0], P[1]), np.hypot(T[0], T[1])
        phase = np.abs(np.angle(np.exp(1j * (np.arctan2(P[1], P[0]) - np.arctan2(T[1], T[0])))))
        w = at / (at.sum() + 1e-12)                          # windows with a real seasonal component matter most
        amp_err = np.minimum(np.abs(ap - at) / (at + 1e-9), 2.0)
        score += float(np.sum(w * (0.5 * amp_err + 0.5 * phase / np.pi))); n += 1
    return score / n if n else float("nan")


def horizon_decay(mse_by_h: dict) -> float:
    """Slope of log(MSE_h / MSE_h0) against log(h/h0): how gracefully accuracy degrades with horizon."""
    hs = sorted(mse_by_h)
    if len(hs) < 2:
        return float("nan")
    x = np.log(np.array(hs) / hs[0])
    y = np.log(np.array([mse_by_h[h] for h in hs]) / mse_by_h[hs[0]])
    return float(np.polyfit(x, y, 1)[0])


# ---------------------------------------------------------------- robustness
PERTURBATIONS = ("white_snr10", "white_snr1", "impulse", "missing_ffill", "level_shift")


def perturb(X: np.ndarray, kind: str, rng: np.random.Generator) -> np.ndarray:
    """Input-side corruption; targets stay clean (cf. noise/SNR taxonomies in recent LTSF robustness benchmarks)."""
    sd = X.std(1, keepdims=True) + 1e-9
    Y = X.copy()
    if kind.startswith("white_snr"):
        snr = float(kind[len("white_snr"):])
        Y += rng.normal(0, 1, X.shape) * sd / math.sqrt(snr)
    elif kind == "impulse":
        k = rng.random(X.shape) < 0.01
        Y[k] += rng.normal(0, 6, k.sum()) * np.broadcast_to(sd, X.shape)[k]
    elif kind == "missing_ffill":
        k = rng.random(X.shape) < 0.05
        k[:, 0] = False
        for j in range(1, X.shape[1]):
            Y[:, j] = np.where(k[:, j], Y[:, j - 1], Y[:, j])
    elif kind == "level_shift":
        Y[:, X.shape[1] // 2:] += sd
    else:
        raise ValueError(kind)
    return Y


def robustness_index(clean_mse: float, perturbed_mse: list[float]) -> float:
    """Geometric mean of MSE(perturbed)/MSE(clean). 1 = unaffected; 2 = errors double on average."""
    return gmre([m / max(clean_mse, 1e-12) for m in perturbed_mse])


# ---------------------------------------------------------------- paired statistics
def _paired(err_a, err_b, min_n: int = 2):
    """Validated float arrays for a paired comparison; anything that could silently yield a verdict raises ValueError."""
    a, b = np.asarray(err_a, float), np.asarray(err_b, float)
    if a.ndim != 1 or a.shape != b.shape:
        raise ValueError(f"paired losses must be 1-D arrays of equal length, got {a.shape} and {b.shape}")
    if a.size < min_n:
        raise ValueError(f"need at least {min_n} paired windows, got {a.size}")
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        raise ValueError("non-finite loss in a paired comparison")
    return a, b


def window_hash(window_ids) -> str:
    """Identity of an ordered evaluation-window set (e.g. (dataset, split, horizon, channel, start) tuples). Two loss vectors
    may be compared pairwise only if their hashes are equal. Duplicated windows are refused (they would let a producer
    over-weight the windows it does well on)."""
    ids = [json_canon(w) for w in window_ids]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate evaluation windows")
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def json_canon(x) -> str:
    import json
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the regularised incomplete beta function (modified Lentz; Numerical Recipes 6.4)."""
    fpmin = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = d if abs(d) > fpmin else fpmin
            c = 1.0 + aa / c
            c = c if abs(c) > fpmin else fpmin
            d = 1.0 / d
            de = d * c
            h *= de
        if abs(de - 1.0) < 3e-16:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom (stdlib only; no scipy in the core)."""
    if math.isnan(t):
        raise ValueError("t is NaN")
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    tail = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t > 0 else 1.0 - tail


def autocorr_time(d: np.ndarray, max_lag: int = 200) -> float:
    """Integrated autocorrelation time 1 + 2*sum(rho_k), summed while rho_k stays above its 95% noise band. Overlapping
    evaluation windows make loss differentials strongly autocorrelated (a window of horizon H shares H-1 points with its
    neighbour); a block shorter than this understates the variance of the mean."""
    d = np.asarray(d, float)
    n = len(d)
    dm = d - d.mean()
    v = float(dm @ dm)
    if n < 8 or v <= 0:
        return 1.0
    tau, band = 1.0, 1.96 / math.sqrt(n)
    for k in range(1, min(max_lag, n // 4) + 1):
        rho = float(dm[k:] @ dm[:-k]) / v
        if rho < band:
            break
        tau += 2 * rho
    return tau


def batch_means_p(d: np.ndarray, block: int) -> float:
    """One-sided p-value (H0: mean(d) <= 0) from non-overlapping batch means and a t distribution with nb-1 degrees of
    freedom: honest about how few independent blocks a short, autocorrelated comparison really has. The batch length is at
    least 3x the autocorrelation time of d, whatever `block` the caller asked for."""
    n = len(d)
    block = max(int(block), int(math.ceil(3 * autocorr_time(d))))
    nb = max(2, n // max(1, block))
    m = np.array([x.mean() for x in np.array_split(d, nb)])
    sd = float(m.std(ddof=1))
    if sd <= 0:
        return 0.0 if m.mean() > 0 else 1.0
    return float(student_t_sf(m.mean() / (sd / math.sqrt(nb)), nb - 1))


def block_bootstrap_pimprove(err_a: np.ndarray, err_b: np.ndarray, block: int = 24, n_boot: int = 400,
                             seed: int = 0) -> dict:
    """Paired, dependence-aware comparison of two forecasters on the SAME windows.
    err_*: per-window losses (e.g. squared error averaged over horizon). Returns P(A better than B), a 90% CI of
    the relative improvement and a one-sided p-value (normal approximation with the bootstrap standard error; H0: A is not
    better than B; the larger of a bootstrap-SE normal p and a batch-means Student-t p, because with few independent blocks the
    normal one is anti-conservative) usable by the online-FDR procedure below. Moving-block resampling keeps overlapping-window autocorrelation
    intact. NaN / inf / mismatched / fewer than 2 windows raise ValueError (never a verdict); a zero-mean reference gives a
    finite (huge) relative improvement because of the 1e-12 guard, so callers must check `mean_ref` themselves."""
    err_a, err_b = _paired(err_a, err_b)
    d = err_b - err_a                                                    # >0: A better
    n = len(d)
    block = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(n / block))
    starts = rng.integers(0, max(1, n - block + 1), size=(n_boot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = d[idx].mean(1)
    mean_ref = float(np.mean(err_b))
    rel = means / max(mean_ref, 1e-12)
    se = float(means.std(ddof=1))
    dbar = float(d.mean())
    p_boot = (0.0 if dbar > 0 else 1.0) if se <= 0 else 0.5 * math.erfc(dbar / se / math.sqrt(2))
    p = max(p_boot, batch_means_p(d, block))                             # the more conservative of two dependence-aware p-values
    return {"p_improve": float((means > 0).mean()), "rel_improvement": float(dbar / max(mean_ref, 1e-12)),
            "ci90": (float(np.quantile(rel, 0.05)), float(np.quantile(rel, 0.95))), "p_value": float(p), "n": n,
            "mean_ref": mean_ref}


def diebold_mariano(err_a: np.ndarray, err_b: np.ndarray, h: int = 1) -> dict:
    """DM test on loss differentials with Newey-West variance (lag h-1) and the Harvey-Leybourne-Newbold correction.
    Positive statistic = A has the smaller loss. Valid for rolling-window (Giacomini-White) forecasts.
    Degenerate input fails closed: fewer than 8 pairs or a constant (all-tied) loss differential returns stat 0, p 1
    ("no evidence"); NaN / inf / length mismatch raises ValueError."""
    err_a, err_b = _paired(err_a, err_b, min_n=0)
    d = err_b - err_a
    n = len(d)
    if n < 8 or np.allclose(d, d[0]):
        return {"stat": 0.0, "p": 1.0, "n": n}
    dm = d - d.mean()
    lag = max(0, h - 1)
    var = float(np.dot(dm, dm) / n)
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * float(np.dot(dm[k:], dm[:-k]) / n)
    var = max(var, 1e-18) / n
    stat = d.mean() / math.sqrt(var)
    hln = math.sqrt(max((n + 1 - 2 * h + h * (h - 1) / n) / n, 0.0))
    stat *= hln
    p = math.erfc(abs(stat) / math.sqrt(2))                                # normal approximation, two-sided
    return {"stat": float(stat), "p": float(p), "n": n}


def indistinguishable_from_best(losses: dict[str, np.ndarray], alpha: float = 0.05, block: int = 24) -> list[str]:
    """Pragmatic 'superior set': the best model plus every model the best is NOT significantly better than
    (paired block bootstrap, one-sided, Bonferroni over the number of rivals). Mirrors the intent of a Model Confidence Set."""
    names = list(losses)
    best = min(names, key=lambda k: float(np.mean(losses[k])))
    keep = [best]
    for k in names:
        if k == best:
            continue
        r = block_bootstrap_pimprove(losses[best], losses[k], block=block)
        if (1 - r["p_improve"]) * (len(names) - 1) > alpha:               # cannot reject "k is as good as best"
            keep.append(k)
    return keep


# ---------------------------------------------------------------- frontier & ranking
def pareto_front(points: list[tuple]) -> list[int]:
    """Indices of non-dominated points (all coordinates minimised)."""
    out = []
    for i, p in enumerate(points):
        if not any(all(q[j] <= p[j] for j in range(len(p))) and any(q[j] < p[j] for j in range(len(p)))
                   for k, q in enumerate(points) if k != i):
            out.append(i)
    return out


def frontier_area(points: list[tuple], ref: tuple) -> float:
    """2-D hypervolume of (log10 params, GMRE) points w.r.t. a reference corner: one number for 'how good is this
    arm's whole size-vs-error frontier'. Larger is better."""
    pts = sorted((x, y) for x, y in points if x < ref[0] and y < ref[1])
    area, best_y, prev_x = 0.0, ref[1], None
    for x, y in pts:
        if y < best_y:
            if prev_x is not None:
                area += (x - prev_x) * (ref[1] - best_y)
            prev_x, best_y = x, y
        elif prev_x is None:
            prev_x = x
    if prev_x is not None:
        area += (ref[0] - prev_x) * (ref[1] - best_y)
    return float(area)


def bradley_terry(matches: list[tuple[str, str]], iters: int = 200) -> dict[str, float]:
    """Bradley-Terry strengths from (winner, loser) pairs (MM algorithm); returned as an Elo-like rating (400*log10).
    Used for the live 'beauty contest' view only; decisions never depend on it."""
    names = sorted({x for m in matches for x in m})
    wins = {n: 1e-3 for n in names}
    cnt: dict = {}
    for w, l in matches:
        wins[w] += 1
        cnt[(w, l)] = cnt.get((w, l), 0) + 1
        cnt[(l, w)] = cnt.get((l, w), 0)
    s = {n: 1.0 for n in names}
    for _ in range(iters):
        new = {}
        for i in names:
            den = sum((cnt.get((i, j), 0) + cnt.get((j, i), 0)) / (s[i] + s[j]) for j in names if j != i)
            new[i] = wins[i] / den if den > 0 else s[i]
        g = math.exp(sum(math.log(v) for v in new.values()) / len(new))
        s = {k: v / g for k, v in new.items()}
    return {k: 1500 + 400 * math.log10(v) for k, v in s.items()}


# ---------------------------------------------------------------- online false-discovery-rate control (promotion tests)
def lord_gamma(t: int) -> float:
    """LORD++ spending sequence (Ramdas et al. 2017): positive, sums to 1 over t >= 1; zero for t <= 0."""
    if t <= 0:
        return 0.0
    return 0.07720838 * math.log(max(t, 2)) / (t * math.exp(math.sqrt(math.log(t))))


def lord_level(t: int, rejections, alpha: float = 0.05, w0: float | None = None) -> float:
    """Significance level of test number t (1-based) given the times of all earlier rejections (= promotions), LORD++:
        alpha_t = gamma_t*w0 + (alpha - w0)*gamma_{t-tau_1} + alpha * sum_{j>=2} gamma_{t-tau_j}.
    Controls the FDR at `alpha` over the whole, unbounded stream of challenges when the p-values of true nulls are
    (super-)uniform and independent; every promotion earns back testing power, every non-promotion spends it."""
    w0 = alpha / 2 if w0 is None else w0
    if not 0 < w0 <= alpha < 1:
        raise ValueError("need 0 < w0 <= alpha < 1")
    taus = sorted(int(x) for x in rejections)
    if any(x >= t for x in taus):
        raise ValueError("rejection times must precede the current test")
    lvl = lord_gamma(t) * w0
    for j, tau in enumerate(taus):
        lvl += (alpha - w0 if j == 0 else alpha) * lord_gamma(t - tau)
    return lvl


# ---------------------------------------------------------------- trivial forecasters (negative-control reference)
def trivial_mses(X: np.ndarray, Y: np.ndarray, period: int = 1) -> dict:
    """MSE of the forecasts that carry no skill: last value, input-window mean, zero (= the training mean on standardised
    data), and seasonal naive at `period`. X: (n, L) inputs, Y: (n, H) targets. Comparing a model with the seasonal naive
    alone is NOT a no-skill test: on white noise the zero forecast already scores rel_mse ~0.5 and on a random walk the
    last value ~0.8, both 'beating the reference' with no information at all."""
    H, p = Y.shape[1], max(1, min(period, X.shape[1]))
    f = {"last": np.repeat(X[:, -1:], H, 1), "window_mean": np.repeat(X.mean(1, keepdims=True), H, 1),
         "zero": np.zeros_like(Y), "snaive": X[:, -p:][:, np.arange(H) % p]}
    return {k: float(np.mean((v - Y) ** 2)) for k, v in f.items()}


def beats_trivial(model_mse: float, X: np.ndarray, Y: np.ndarray, period: int = 1, margin: float = 0.0) -> dict:
    """Skill gate: True only if the model is better than EVERY trivial forecaster by at least `margin` (relative).
    On a negative-control dataset (white noise, random walk, shuffled real data) the expected answer is False."""
    t = trivial_mses(X, Y, period)
    best = min(t.values())
    return {"ok": bool(np.isfinite(model_mse) and model_mse < best * (1.0 - margin)), "model_mse": float(model_mse),
            "best_trivial": best, "best_trivial_name": min(t, key=t.get), "trivial": t}
