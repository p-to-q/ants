"""Synthetic data suite: known generating logic, known answers.

Three kinds, all with role 'stress' in the catalog (they never enter the headline score):
  mechanism        different generating logics (regimes, chaos, calendar effects, intermittency, heavy tails, long memory,
                   chirps, cross-channel coupling, non-sinusoidal waves) - where do period-folding / spectral / linear models break?
  negative control no model may show skill: white noise (best forecast = mean), random walk (best = last value). Skill here means
                   leakage or overfitting. These are E0 integrity probes.
  extreme          degenerate or hostile inputs (huge/tiny scale, constant channels, missing blocks, outlier bursts, end-of-series
                   level shift, very short series, quantisation, exponential growth, sign-flipping seasonality).
Every generator is a pure function of (seed, T, C): regenerating gives byte-identical arrays. `info` records the truth
(e.g. noise fraction => oracle MSE floor) so metrics can be checked against the known answer.
"""
from __future__ import annotations

import numpy as np

T0, C0 = 14400, 7                         # ETTh-like length and channel count


def _sines(rng, T, C, periods=((24, .5, 1.5), (168, .2, 1.0), (12, 0., .4))):
    t = np.arange(T)
    out = np.zeros((T, C))
    for c in range(C):
        for per, lo, hi in periods:
            out[:, c] += rng.uniform(lo, hi) * np.sin(2 * np.pi * t / per + rng.uniform(0, 6.28))
    return out


def _ar(rng, T, C, phi=0.85, sd=0.35):
    e = rng.normal(0, sd, (T, C))
    x = np.zeros((T, C))
    for i in range(1, T):
        x[i] = phi * x[i - 1] + e[i]
    return x


# ------------------------------------------------------------------ mechanism families
def sines_ar(rng, T=T0, C=C0):
    return _sines(rng, T, C) + _ar(rng, T, C), {}


def snr(rng, T=T0, C=C0, level=10.0):
    s = _sines(rng, T, C)
    sig_var = s.var(0)
    noise = rng.normal(0, 1, (T, C)) * np.sqrt(sig_var / level)
    return s + noise, {"snr": level, "oracle_mse_floor_standardised": 1.0 / (1.0 + level)}


def regime(rng, T=T0, C=C0):
    params = [(24, 1.0, 0.2), (48, 0.5, 0.6), (12, 1.5, 0.1)]
    state, out, t = 0, np.zeros((T, C)), np.arange(T)
    ph = rng.uniform(0, 6.28, C)
    for i in range(T):
        if rng.random() > 0.995:
            state = rng.integers(0, 3)
        per, amp, sd = params[state]
        out[i] = amp * np.sin(2 * np.pi * i / per + ph) + rng.normal(0, sd, C)
    return out, {"regimes": 3}


def lorenz(rng, T=T0, C=C0):
    def f(v): x, y, z = v; return np.array([10 * (y - x), x * (28 - z) - y, x * y - 8 / 3 * z])
    v = rng.uniform(-8, 8, 3) + np.array([0, 0, 25.0]); h, k, out = 0.01, 5, np.zeros((T, 3))
    for _ in range(2000):                                   # burn-in onto the attractor
        a = f(v); b = f(v + h / 2 * a); c_ = f(v + h / 2 * b); d = f(v + h * c_); v = v + h / 6 * (a + 2 * b + 2 * c_ + d)
    for i in range(T):
        for _ in range(k):
            a = f(v); b = f(v + h / 2 * a); c_ = f(v + h / 2 * b); d = f(v + h * c_); v = v + h / 6 * (a + 2 * b + 2 * c_ + d)
        out[i] = v
    return out, {"deterministic_chaos": True, "channels": 3}


def mackey_glass(rng, T=T0, C=1, tau=17):
    n, x = T * 3 + 400, np.ones(T * 3 + 400 + tau) * 1.2
    x[:tau + 1] += rng.uniform(-0.05, 0.05, tau + 1)
    for i in range(tau, len(x) - 1):
        x[i + 1] = x[i] + (0.2 * x[i - tau] / (1 + x[i - tau] ** 10) - 0.1 * x[i])
    return x[400 + tau::3][:T, None], {"deterministic_chaos": True, "tau": tau}


def trend_calendar(rng, T=T0, C=C0):
    t = np.arange(T)
    base = _sines(rng, T, C, ((24, .6, 1.2), (168, .4, .9), (8766, .5, 1.0)))
    trend = (0.0003 * t[:, None] + 2e-9 * t[:, None] ** 2) * rng.uniform(.5, 1.5, C)
    day = (t // 24) % 365
    holidays = np.isin(day, [0, 100, 150, 250, 330])               # fixed calendar effects (known dates)
    hol = -1.5 * holidays[:, None] * (np.abs(np.sin(2 * np.pi * t / 24))[:, None])
    level = base + trend + hol
    noise = rng.normal(0, 1, (T, C)) * (0.15 + 0.1 * np.abs(level))   # heteroscedastic
    return level + noise, {"calendar_effects": True}


def intermittent(rng, T=T0, C=C0):
    t = np.arange(T)[:, None]
    lam = 3.0 * (1 + 0.8 * np.sin(2 * np.pi * t / 24 + rng.uniform(0, 6, C)))
    demand = rng.poisson(lam).astype(float)
    on = rng.random((T, C)) < 0.35                                    # hurdle: ~65% structural zeros
    return demand * on, {"zero_fraction": float(1 - on.mean())}


def heavy_tail(rng, T=T0, C=C0):
    return _sines(rng, T, C) + 0.4 * rng.standard_t(2.2, (T, C)), {"df": 2.2}


def long_memory(rng, T=T0, C=C0, H=0.9):
    k = np.arange(T // 2 + 1); k[0] = 1
    amp = k ** (-(2 * H + 1) / 2.0)
    out = np.zeros((T, C))
    for c in range(C):
        spec = amp * (rng.normal(size=len(k)) + 1j * rng.normal(size=len(k)))
        out[:, c] = np.fft.irfft(spec, n=T)
    return out, {"hurst": H}


def chirp(rng, T=T0, C=C0):
    t = np.arange(T)[:, None] / T
    f0, f1 = rng.uniform(40, 80, C), rng.uniform(250, 450, C)         # cycles over the series, increasing
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t ** 2 / 2)
    return (1 + 0.5 * np.sin(2 * np.pi * 7 * t)) * np.sin(phase) + rng.normal(0, .2, (T, C)), {"period_drifts": True}


def coupled(rng, T=T0, C=C0):
    drv = _sines(rng, T, 1)[:, 0] + _ar(rng, T, 1, .9, .5)[:, 0]
    out = np.zeros((T, C)); out[:, 0] = drv
    for c in range(1, C):                                             # channel c is channel 0 delayed by 3c steps
        out[:, c] = np.roll(drv, 3 * c) * rng.uniform(.6, 1.2) + rng.normal(0, .15, T)
    return out, {"cross_channel": True}


def sawtooth_square(rng, T=T0, C=C0):
    t = np.arange(T)[:, None]
    saw = 2 * ((t / 24 + rng.uniform(0, 1, C)) % 1) - 1
    sq = np.sign(np.sin(2 * np.pi * t / 48 + rng.uniform(0, 6, C)))
    return 0.7 * saw + 0.5 * sq + rng.normal(0, .25, (T, C)), {"non_sinusoidal": True}


# ------------------------------------------------------------------ negative controls
def white_noise(rng, T=T0, C=C0):
    return rng.normal(0, 1, (T, C)), {"negative_control": "mean_is_optimal", "oracle_mse_floor_standardised": 1.0}


def random_walk(rng, T=T0, C=C0):
    return np.cumsum(rng.normal(0, 1, (T, C)), 0), {"negative_control": "last_value_is_optimal"}


# ------------------------------------------------------------------ extreme / hostile
def scale_huge(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]; return x * 1e6 + 1e9, {"extreme": "scale"}


def scale_tiny(rng, T=T0, C=C0):
    return sines_ar(rng, T, C)[0] * 1e-9, {"extreme": "scale"}


def constant_channels(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]; x[:, ::2] = 3.0; return x, {"extreme": "zero_variance_channels", "constant": int(np.ceil(C / 2))}


def missing_blocks(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]
    for _ in range(40):
        s = rng.integers(0, T - 60); x[s:s + rng.integers(5, 60), rng.integers(0, C)] = np.nan
    return x, {"extreme": "nan_blocks"}


def outlier_burst(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]; sd = x.std(0)
    for _ in range(int(0.01 * T)):
        s, c = rng.integers(0, T - 5), rng.integers(0, C); x[s:s + rng.integers(1, 5), c] += rng.choice([-1, 1]) * rng.uniform(10, 50) * sd[c]
    return x, {"extreme": "bursts"}


def shift_end(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]; x[int(0.95 * T):] += 5 * x.std(0); return x, {"extreme": "test_region_level_shift"}


def very_short(rng, T=600, C=C0):
    return sines_ar(rng, T, C)[0], {"extreme": "short"}


def quantised(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]; q = (x.max(0) - x.min(0)) / 7; return np.round(x / q) * q, {"extreme": "8_levels"}


def exp_growth(rng, T=T0, C=C0):
    t = np.arange(T)[:, None]
    return np.exp(0.0004 * t) * (1 + 0.3 * np.sin(2 * np.pi * t / 24 + rng.uniform(0, 6, C))) + rng.normal(0, .05, (T, C)), {"extreme": "exponential_growth"}


def sign_chaos(rng, T=T0, C=C0):
    t = np.arange(T)[:, None]
    sign = np.repeat(rng.choice([-1.0, 1.0], (T // 24 + 1, C)), 24, axis=0)[:T]
    return sign * np.sin(2 * np.pi * t / 24) + rng.normal(0, .1, (T, C)), {"extreme": "random_daily_sign"}


# ------------------------------------------------------------------ model-generated (classical stochastic models)
def arima(rng, T=T0, C=C0):
    out = np.zeros((T, C))
    for c in range(C):
        while True:                                                          # stationarity triangle for AR(2): never explosive
            phi = rng.uniform(-.6, .8, 2) * np.array([1, .5])
            if phi[1] + phi[0] < .98 and phi[1] - phi[0] < .98 and abs(phi[1]) < .98:
                break
        th = rng.uniform(-.5, .5, 2)
        e = rng.normal(0, 1, T + 2); x = np.zeros(T)
        for i in range(2, T):
            x[i] = phi[0] * x[i - 1] + phi[1] * x[i - 2] + e[i] + th[0] * e[i - 1] + th[1] * e[i - 2]
        out[:, c] = np.cumsum(x) * 0.05 if c % 2 else x          # half integrated (d=1), half stationary ARMA(2,2)
    return out, {"model": "ARIMA(2,d,2)"}


def sarima(rng, T=T0, C=C0):
    out = np.zeros((T, C))
    for c in range(C):
        e = rng.normal(0, 1, T); x = np.zeros(T); P, ph = rng.uniform(.5, .85), rng.uniform(.2, .6)
        for i in range(25, T):
            x[i] = ph * x[i - 1] + P * x[i - 24] - ph * P * x[i - 25] + e[i]
        out[:, c] = x
    return out, {"model": "SARIMA(1,0,0)x(1,0,0)24"}


def structural(rng, T=T0, C=C0):
    out = np.zeros((T, C))
    for c in range(C):
        lvl, slope, seas = 0.0, 0.0, rng.normal(0, 1, 24)
        for i in range(T):
            slope += rng.normal(0, 0.002); lvl += slope + rng.normal(0, 0.05)
            seas[i % 24] += rng.normal(0, 0.01)
            out[i, c] = lvl + seas[i % 24] + rng.normal(0, 0.3)
    return out, {"model": "basic structural (level+slope+seasonal state)"}


def garch(rng, T=T0, C=C0):
    out = np.zeros((T, C))
    for c in range(C):
        w, a, b = 0.05, rng.uniform(.08, .2), rng.uniform(.7, .85); h, r = 1.0, 0.0
        for i in range(T):
            h = w + a * r * r + b * h; r = np.sqrt(h) * rng.standard_normal(); out[i, c] = r
    out += 0.4 * np.sin(2 * np.pi * np.arange(T)[:, None] / 24)             # volatility clustering on a weak seasonal mean
    return out, {"model": "GARCH(1,1)"}


def hawkes(rng, T=T0, C=C0):
    out = np.zeros((T, C))
    for c in range(C):
        mu, alpha, decay = 0.15, 0.5, 0.9; lam = mu
        for i in range(T):
            n = rng.poisson(lam); out[i, c] = n; lam = mu + decay * (lam - mu) + alpha * n * (1 - decay)
    return out, {"model": "discrete Hawkes (bursty events)"}


def ou_process(rng, T=T0, C=C0):
    th, mu, sg = rng.uniform(.01, .2, C), rng.normal(0, 1, C), rng.uniform(.2, .8, C)
    x = np.zeros((T, C)); x[0] = mu
    for i in range(1, T):
        x[i] = x[i - 1] + th * (mu - x[i - 1]) + sg * rng.standard_normal(C)
    return x, {"model": "Ornstein-Uhlenbeck"}


def weekday_calendar(rng, T=T0, C=C0):
    t = np.arange(T)[:, None]
    wk = ((t // 24) % 7 < 5).astype(float)                                   # weekdays vs weekends differ in amplitude AND shape
    day = np.sin(2 * np.pi * t / 24 + rng.uniform(0, 6, C))
    return wk * 1.2 * np.maximum(day, 0) + (1 - wk) * 0.4 * day + rng.normal(0, .2, (T, C)), {"calendar_gated": True}


# ------------------------------------------------------------------ random nonlinear dynamics (neural-network-generated)
def random_rnn(rng, T=T0, C=C0, N=40):
    W = rng.normal(0, 1, (N, N)) / np.sqrt(N); W *= 1.2 / max(abs(np.linalg.eigvals(W)))     # edge-of-chaos echo-state network
    Win = rng.normal(0, 1, (N, 2)); Wout = rng.normal(0, 1, (N, C)); x = np.zeros(N); out = np.zeros((T, C))
    u = np.stack([np.sin(2 * np.pi * np.arange(T) / 24), rng.normal(0, .3, T)], 1)
    for i in range(T):
        x = np.tanh(W @ x + Win @ u[i]); out[i] = x @ Wout
    return out, {"model": "random echo-state network"}


def random_nonlinear_ar(rng, T=T0, C=C0, p=6):
    W1 = rng.normal(0, 1, (16, p * C)) / np.sqrt(p * C) * 1.6; W2 = rng.normal(0, 1, (C, 16)) / 4.0
    x = np.zeros((T, C)); x[:p] = rng.normal(0, 1, (p, C))
    for i in range(p, T):
        x[i] = np.tanh(W2 @ np.tanh(W1 @ x[i - p:i][::-1].ravel())) * 1.5 + 0.2 * rng.standard_normal(C)
    return x, {"model": "random MLP autoregression"}


# ------------------------------------------------------------------ compositional (Chronos-KernelSynth-style) and hybrid mixtures
def _component(rng, T):
    kind = rng.choice(["periodic", "smooth", "linear", "white", "ar", "step", "spikes"])
    t = np.arange(T)
    if kind == "periodic":
        per = rng.choice([8, 12, 24, 48, 96, 168, 365]); return np.sin(2 * np.pi * t / per + rng.uniform(0, 6.28)) * rng.uniform(.3, 1.5)
    if kind == "smooth":
        k = int(rng.choice([24, 96, 400])); z = np.convolve(rng.normal(0, 1, T + k), np.ones(k) / np.sqrt(k), "valid")[:T]; return z / (z.std() + 1e-9)
    if kind == "linear":
        return (t / T - .5) * rng.uniform(-3, 3)
    if kind == "white":
        return rng.normal(0, rng.uniform(.1, .6), T)
    if kind == "ar":
        return _ar(rng, T, 1, rng.uniform(.5, .95), .3)[:, 0]
    if kind == "step":
        x = np.zeros(T); cps = np.sort(rng.integers(0, T, rng.integers(2, 8)))
        for cp_ in cps: x[cp_:] += rng.normal(0, 1)
        return x
    x = np.zeros(T); k = rng.random(T) < .003; x[k] = rng.normal(0, 4, k.sum()); return x


def kernel_synth(rng, T=T0, C=C0):
    out = np.zeros((T, C)); progs = []
    for c in range(C):
        acc = _component(rng, T); prog = ["c"]
        for _ in range(rng.integers(1, 4)):
            comp = _component(rng, T)
            if rng.random() < .4: acc = acc * (1 + .5 * comp); prog.append("x")
            else: acc = acc + comp; prog.append("+")
        out[:, c] = acc; progs.append("".join(prog))
    return out, {"programs": progs}


def hybrid(rng, T=T0, C=C0):
    pool = [sines_ar, regime, heavy_tail, trend_calendar, chirp, sawtooth_square, long_memory, weekday_calendar, ou_process, sarima]
    out = np.zeros((T, C)); used = []
    for c in range(C):
        k = rng.integers(3, 6); idx = rng.choice(len(pool), k, replace=False); w = rng.dirichlet(np.ones(k))
        for wi, i in zip(w, idx):
            x, _ = pool[i](np.random.default_rng(int(rng.integers(1 << 30))), T, 1)
            out[:, c] += wi * (x[:, 0] - x[:, 0].mean()) / (x[:, 0].std() + 1e-9)
        used.append([pool[i].__name__ for i in idx])
    return out, {"mixtures": used}


# ------------------------------------------------------------------ more dynamical systems
def rossler(rng, T=T0, C=C0):
    def f(v): x, y, z = v; return np.array([-y - z, x + .2 * y, .2 + z * (x - 5.7)])
    v, h, out = rng.uniform(-3, 3, 3), 0.05, np.zeros((T, 3))
    for _ in range(3000): v = v + h * f(v + h / 2 * f(v))
    for i in range(T):
        for _ in range(4): v = v + h * f(v + h / 2 * f(v))
        out[i] = v
    return out, {"deterministic_chaos": True}


def van_der_pol(rng, T=T0, C=C0, mu=3.0):
    def f(v): x, y = v; return np.array([y, mu * (1 - x * x) * y - x])
    v, h, out = rng.uniform(-2, 2, 2), 0.02, np.zeros((T, 2))
    for i in range(T):
        for _ in range(10): v = v + h * f(v + h / 2 * f(v))
        out[i] = v
    return out, {"relaxation_oscillator": True}


def lotka_volterra(rng, T=T0, C=C0):
    def f(v): x, y = v; return np.array([x * (.6 - .3 * y), y * (.2 * x - .5)])
    v, h, out = np.array([2.0, 1.0]) * rng.uniform(.8, 1.2, 2), 0.02, np.zeros((T, 2))
    for i in range(T):
        for _ in range(10): v = v + h * f(v + h / 2 * f(v))
        out[i] = v
    return out, {"predator_prey": True}


def henon_logistic(rng, T=T0, C=C0):
    a, x, y, out = 1.4, .1, .1, np.zeros((T, 2))
    for i in range(T):
        x, y = 1 - a * x * x + y, 0.3 * x; out[i] = (x, y)
    r, z, lg = 3.9, rng.uniform(.2, .8), np.zeros((T, 1))
    for i in range(T): z = r * z * (1 - z); lg[i] = z
    return np.concatenate([out, lg], 1), {"deterministic_chaos": True, "discrete_maps": True}


# ------------------------------------------------------------------ degenerate / piecewise
def all_constant(rng, T=T0, C=C0):
    return np.tile(rng.uniform(-5, 5, C), (T, 1)), {"extreme": "every channel is one number"}


def piecewise_constant(rng, T=T0, C=C0):
    x = np.zeros((T, C))
    for c in range(C):
        cps = np.sort(rng.integers(1, T, rng.integers(5, 40))); v = rng.normal(0, 1)
        prev = 0
        for cp_ in list(cps) + [T]: x[prev:cp_, c] = v; v = rng.normal(0, 1); prev = cp_
    return x + rng.normal(0, 0.01, (T, C)), {"extreme": "step function"}


def saturated(rng, T=T0, C=C0):
    x = sines_ar(rng, T, C)[0]; lim = np.quantile(x, [.25, .75], axis=0)
    return np.clip(x, lim[0], lim[1]), {"extreme": "values pinned at bounds ~50% of the time"}


def onoff_machine(rng, T=T0, C=C0):
    state, out = np.zeros(C, int), np.zeros((T, C))
    for i in range(T):
        flip = rng.random(C) < .01; state = np.where(flip, 1 - state, state)
        out[i] = state * (2 + np.sin(2 * np.pi * i / 24)) + rng.normal(0, .05, C)
    return out, {"extreme": "bimodal on/off levels"}


def staircase_trend(rng, T=T0, C=C0):
    x = np.cumsum((rng.random((T, C)) < .002) * rng.uniform(.5, 2, (T, C)), 0)
    return x, {"extreme": "monotone staircase (the one family where only the trend matters)"}


# ------------------------------------------------------------------ reality-calibrated surrogates of REAL data (need a base array)
def phase_randomised(base: np.ndarray, rng, T=None):
    """Same power spectrum and (near) marginal distribution as the real series, nonlinear/temporal-phase structure destroyed."""
    T = T or len(base); out = np.zeros((T, base.shape[1]))
    for c in range(base.shape[1]):
        x = base[:T, c]; F = np.fft.rfft(x - x.mean()); ph = np.exp(1j * rng.uniform(0, 2 * np.pi, len(F))); ph[0] = 1
        if T % 2 == 0:
            ph[-1] = 1                                                   # the Nyquist bin of an even-length real signal must stay real
        out[:, c] = np.fft.irfft(np.abs(F) * ph, n=T) + x.mean()
    return out


def block_bootstrap(base: np.ndarray, rng, T=None, block=96):
    T = T or len(base); idx = []
    while len(idx) < T:
        s = int(rng.integers(0, len(base) - block)); idx += list(range(s, s + block))
    return base[np.array(idx[:T])]


def ar_simulation(base: np.ndarray, rng, T=None, p=24):
    """Fit AR(p) per channel to the real series and simulate with bootstrapped residuals: linear dynamics of the real data, nothing else."""
    T = T or len(base); out = np.zeros((T, base.shape[1]))
    for c in range(base.shape[1]):
        x = base[:, c] - base[:, c].mean(); X = np.stack([x[p - k - 1:len(x) - k - 1] for k in range(p)], 1); y = x[p:]
        coef = np.linalg.lstsq(X, y, rcond=None)[0]; res = y - X @ coef; z = list(x[:p])
        for i in range(T):
            z.append(float(np.dot(coef, z[-1:-p - 1:-1]) + rng.choice(res)))
        out[:, c] = np.array(z[p:p + T]) + base[:, c].mean()
    return out


def shuffled(base: np.ndarray, rng, T=None):
    T = T or len(base); return base[rng.permutation(len(base))[:T]]


REAL_BASED = {"phase_randomised": phase_randomised, "block_bootstrap": block_bootstrap, "ar_simulation": ar_simulation, "shuffled": shuffled}


GENERATORS = {
    "mechanism": dict(sines_ar=sines_ar, regime=regime, lorenz=lorenz, mackey_glass=mackey_glass, trend_calendar=trend_calendar,
                      intermittent=intermittent, heavy_tail=heavy_tail, long_memory=long_memory, chirp=chirp, coupled=coupled,
                      sawtooth_square=sawtooth_square, arima=arima, sarima=sarima, structural=structural, garch=garch, hawkes=hawkes,
                      ou_process=ou_process, weekday_calendar=weekday_calendar, random_rnn=random_rnn,
                      random_nonlinear_ar=random_nonlinear_ar, kernel_synth=kernel_synth, hybrid=hybrid, rossler=rossler,
                      van_der_pol=van_der_pol, lotka_volterra=lotka_volterra, henon_logistic=henon_logistic),
    "negative_control": dict(white_noise=white_noise, random_walk=random_walk),
    "extreme": dict(scale_huge=scale_huge, scale_tiny=scale_tiny, constant_channels=constant_channels, missing_blocks=missing_blocks,
                    outlier_burst=outlier_burst, shift_end=shift_end, very_short=very_short, quantised=quantised,
                    exp_growth=exp_growth, sign_chaos=sign_chaos, all_constant=all_constant, piecewise_constant=piecewise_constant,
                    saturated=saturated, onoff_machine=onoff_machine, staircase_trend=staircase_trend),
}


def generate(family: str, seed: int, **kw):
    for kind, d in GENERATORS.items():
        if family in d:
            x, info = d[family](np.random.default_rng(seed), **kw)
            return x, {**info, "kind": kind}
    if family.startswith("snr"):
        x, info = snr(np.random.default_rng(seed), **{"level": float(family[3:])})
        return x, {**info, "kind": "mechanism"}
    raise KeyError(family)


REAL_BASES = ("etth1", "weather", "solar", "electricity")


def catalog_toml(seeds=(1, 2)) -> str:
    """Catalog entries (role = stress) for every generator; written to data/catalog_synth.toml."""
    out = ["# GENERATED by synth_suite.catalog_toml(): synthetic suite, role=stress (gates and diagnostics, never the headline)\n"]
    fams = [(k, f) for k, d in GENERATORS.items() for f in d] + [("mechanism", f"snr{lv}") for lv in (100, 10, 1, 0.1)]
    for kind, fam in fams:
        for sd in (seeds if kind != "extreme" else seeds[:1]):
            T = 600 if fam == "very_short" else T0
            split = [360, 120, 120] if fam == "very_short" else [8640, 2880, 2880]
            hor = [24, 48] if fam == "very_short" else [96, 192, 336, 720]
            out.append(f'[dataset.syn_{fam.replace(".", "_")}_{sd}]\nfamily = "synthetic_{fam.replace(".", "_")}"\nrole = "stress"\nraw = "synthetic"\n'
                       f'format = "synth"\nsynth_family = "{fam}"\nsynth_seed = {sd}\nsplit = {split}\nref_period = 24\nhorizons = {hor}\n'
                       f'note = "{kind}: see synth_suite.py"\nsource = "generated"\nlicense = "n/a (generated)"\n')
    for kind in REAL_BASED:                                          # reality-calibrated surrogates derived from real dev/confirm/sealed data
        for base in REAL_BASES:
            out.append(f'[dataset.syn_{kind}_{base}]\nfamily = "synthetic_{kind}"\nrole = "stress"\nraw = "synthetic"\n'
                       f'format = "synth_real"\nsynth_kind = "{kind}"\nsynth_base = "{base}"\nsynth_seed = 1\nsplit = [0.7, 0.1, 0.2]\n'
                       f'ref_period = 24\nhorizons = [96, 192, 336]\nnote = "surrogate of {base}: {kind}"\nsource = "generated from {base}"\n'
                       f'license = "derived from {base}"\n')
    return "\n".join(out)
