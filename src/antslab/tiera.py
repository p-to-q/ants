"""Tier A report: per-dataset, per-horizon MSE and MAE on z-scored validation windows,
with the model's own lookback, for frozen specifications.  Report-only: never writes to
the ledger or feeds the search.

The numbers use the catalog's chronological splits and the gym's own fit path
(evaluate_catalog with uncapped windows).  They are NOT comparable with published tables
until the full Tier A harness of docs/benchmark.md section 13 is run (fixed lookback,
standard batch handling, bootstrap intervals).  The report states which protocol items
differ.

Hard rules
----------
* VALIDATION split of DEVELOPMENT datasets only.  --split test and non-dev datasets are
  refused with a message naming the gate (src/antslab/gate.py).
* Never writes to the ledger or the search state.
* States in its output that the numbers differ from published tables.
* Deterministic: same inputs, same numbers.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

# re-use the gym's own model and scoring; never re-implement
from .gyms.tinyts import (
    Model, _score, bits, charged_params, count_params, eff_len, g, members,
    PRIOR_CORES, _prior_model, _pool_prior, PROFILES, PARAM_H,
)

HORIZONS = [96, 192, 336, 720]


def _refuse_non_val(split: str) -> None:
    if split != "val":
        raise SystemExit(
            f"ants tier-a: --split {split!r} refused.  Test windows of development data "
            "and confirm/sealed data belong to the gate (src/antslab/gate.py).  "
            "This tool reports validation-split numbers only."
        )


def _refuse_non_dev(names: list[str], cat) -> None:
    bad = [n for n in names if cat.meta[n]["role"] != "dev"]
    if bad:
        raise SystemExit(
            f"ants tier-a: datasets {bad} are not development role.  "
            "Confirm and sealed data belong to the gate (src/antslab/gate.py)."
        )


def _fit_and_score(spec, ser, cat, horizons, folds, split="val"):
    """Fit and score one spec on one dataset, returning per-horizon rows.
    Reuses the gym's own code path: Model, _score, sample_windows, rolling_blocks."""
    from .data import sample_windows
    from .gyms.tinyts import rolling_blocks

    d = ser.standardised()
    clip = g(spec, "clip")
    cd = np.clip(d, -g(spec, "clip_z"), g(spec, "clip_z")) if clip else d
    d32 = d.astype(np.float32)
    cd32 = cd.astype(np.float32) if clip else d32
    n_d = len(d)
    del d, cd

    blocks = rolling_blocks(ser, folds) if split == "val" else [(ser.n_train + ser.n_val, n_d)]
    priors: dict = {}
    rows = []

    for H in horizons:
        fold_mses, fold_maes, fold_ref_mses, fold_ref_maes = [], [], [], []
        for fi, (lo, hi) in enumerate(blocks):
            tr_end = ser.n_train if split != "val" or folds <= 1 else lo
            if hi - lo < H + 8 or tr_end < 2 * H + 16:
                continue
            members_ = members(spec)
            Lm = [min(eff_len(m["lookback"], m["period"]), max(m["period"], (tr_end - H) // 3))
                  for m in members_]
            Lmax = max(max(Lm), ser.ref_period)
            fitted = []
            for ms, L in zip(members_, Lm):
                ms = {**ms, "lookback": L}
                # UNCAPPED windows: pass cap=None for all validation windows
                Xt, Yt, tt = sample_windows(
                    cd32, 0, tr_end, L, H,
                    cap=None, seed=11 + fi, with_t=True,
                )
                w = np.exp(g(ms, "recency") * (tt - 1.0)) if g(ms, "recency") else None
                aug = (np.random.default_rng(12345), g(ms, "aug_sigma")) if g(ms, "augment") else None
                prior = None
                if g(ms, "pretrain") == "synth" and ms["core"] in PRIOR_CORES:
                    prior = _prior_model({**ms, "lookback": L}, H, 4, PROFILES["hard"][1], priors)
                elif g(ms, "pretrain") == "pool" and ms["core"] in PRIOR_CORES:
                    prior = _pool_prior({**ms, "lookback": L}, L, H, ser.family, cat)
                fitted.append(Model(ms).fit(Xt, Yt, w, prior, aug).post_train())
                del Xt, Yt, tt, w
            # UNCAPPED validation windows
            Xv, Yv = sample_windows(cd32, lo, hi, Lmax, H, cap=None, seed=21 + fi)
            Xraw = Xv
            if clip:
                Xraw, Yv = sample_windows(d32, lo, hi, Lmax, H, cap=None, seed=21 + fi)
            mse, mae, ref_mse, ref_mae, _ = _score(
                fitted, Xv, Yv, H, min(ser.ref_period, Xv.shape[1]), keep=False, Xref=Xraw,
            )
            fold_mses.append(mse)
            fold_maes.append(mae)
            fold_ref_mses.append(ref_mse)
            fold_ref_maes.append(ref_mae)
            del Xv, Xraw, Yv, fitted

        if not fold_mses:
            continue
        avg_mse = float(np.mean(fold_mses))
        avg_mae = float(np.mean(fold_maes))
        avg_ref_mse = float(np.mean(fold_ref_mses))
        avg_ref_mae = float(np.mean(fold_ref_maes))
        rows.append({
            "dataset": ser.name,
            "horizon": H,
            "mse_std": avg_mse,
            "mae_std": avg_mae,
            "naive_mse": avg_ref_mse,
            "naive_mae": avg_ref_mae,
            "rel_mse": avg_mse / max(avg_ref_mse, 1e-12),
            "rel_mae": avg_mae / max(avg_ref_mae, 1e-12),
        })
    return rows


def tier_a_report(specs: list[dict], split: str = "val",
                  lookback_override: int | None = None) -> dict:
    """Run one Tier A report for a list of specs.
    Returns {"protocol": str, "specs": [{"spec": ..., "rows": [...], "avg": {...}, ...}]}"""
    _refuse_non_val(split)
    if lookback_override is not None and (not isinstance(lookback_override, int)
                                         or isinstance(lookback_override, bool) or lookback_override <= 0):
        raise ValueError("lookback must be a positive integer")

    from .gyms.tinyts import catalog as get_catalog
    cat = get_catalog()
    dev_names = cat.names("dev")
    _refuse_non_dev(dev_names, cat)

    # folds: use the catalog stage s2 convention (3 folds for validation)
    from .gyms.tinyts import CAT_STAGES
    folds = CAT_STAGES["s2"].get("folds", 3)

    protocol_lines = [
        "Protocol: Tier A validation report",
        f"  Split: validation ({folds} expanding-window folds)",
        "  Standardisation: z-score with training-set statistics",
        "  Windows: uncapped (all validation windows scored)",
        "  No dropped last batch",
        "  Validation split of development datasets only",
        f"  Parameter counts use reference horizon {PARAM_H}; this is refitting, not a saved checkpoint",
        "",
        "NOTE: these numbers are NOT comparable with published tables.",
        "Differences from the standard LTSF protocol (benchmark.md section 13):",
        "  - lookback: the model's own (stated per spec), not fixed 96 or 336",
        "  - no bootstrap intervals",
        "  - no MASE (planned for epoch 3)",
        "  - the evaluator's chunking may differ from papers' batch handling",
    ]

    results = []
    for spec in specs:
        if lookback_override is not None:
            spec = {**spec, "lookback": lookback_override}
        npar = count_params(spec)
        nbytes = int(npar * bits(spec) / 8)
        lb = spec["lookback"]
        all_rows = []
        t0 = time.time()
        for name in dev_names:
            ser = cat.load(name)
            horizons = [h for h in HORIZONS if h in ser.horizons or h <= ser.n_val]
            if not horizons:
                horizons = ser.horizons
            rows = _fit_and_score(spec, ser, cat, horizons, folds, split)
            all_rows.extend(rows)
        fit_s = time.time() - t0

        # per-dataset averages
        by_ds: dict = {}
        for r in all_rows:
            by_ds.setdefault(r["dataset"], []).append(r)
        ds_avgs = {}
        for ds, rs in by_ds.items():
            ds_avgs[ds] = {
                "mse_std": float(np.mean([r["mse_std"] for r in rs])),
                "mae_std": float(np.mean([r["mae_std"] for r in rs])),
                "rel_mse": float(np.mean([r["rel_mse"] for r in rs])),
                "rel_mae": float(np.mean([r["rel_mae"] for r in rs])),
            }

        results.append({
            "spec": spec,
            "lookback": lb,
            "n_params": npar,
            "bytes": nbytes,
            "rows": all_rows,
            "dataset_avgs": ds_avgs,
            "fit_s": round(fit_s, 1),
        })

    return {"protocol": "\n".join(protocol_lines), "specs": results}


def format_markdown(report: dict) -> str:
    """Render the report as Markdown text."""
    lines = [report["protocol"], ""]
    for si, sr in enumerate(report["specs"]):
        spec = sr["spec"]
        lines.append(f"## Spec {si + 1}: lookback={sr['lookback']}  n_params={sr['n_params']}  bytes={sr['bytes']}")
        lines.append(f"  core={spec['core']}  period={spec['period']}  norm={spec['norm']}  ridge={spec['ridge']}")
        lines.append(f"  fit time: {sr['fit_s']} s")
        lines.append("")
        lines.append("| dataset | horizon | MSE (std.) | MAE (std.) | naive MSE | naive MAE | rel_mse | rel_mae |")
        lines.append("|---------|---------|------------|------------|-----------|-----------|---------|---------|")
        for r in sr["rows"]:
            lines.append(f"| {r['dataset']} | {r['horizon']} | {r['mse_std']:.4f} | {r['mae_std']:.4f} "
                         f"| {r['naive_mse']:.4f} | {r['naive_mae']:.4f} | {r['rel_mse']:.4f} | {r['rel_mae']:.4f} |")
        lines.append("")
        lines.append("Per-dataset averages (mean over horizons):")
        lines.append("")
        lines.append("| dataset | MSE (std.) | MAE (std.) | rel_mse | rel_mae |")
        lines.append("|---------|------------|------------|---------|---------|")
        for ds, a in sr["dataset_avgs"].items():
            lines.append(f"| {ds} | {a['mse_std']:.4f} | {a['mae_std']:.4f} | {a['rel_mse']:.4f} | {a['rel_mae']:.4f} |")
        lines.append("")
    return "\n".join(lines)


def format_csv(report: dict) -> str:
    """Render all rows as CSV."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["spec_idx", "lookback", "n_params", "bytes", "dataset", "horizon",
                "mse_std", "mae_std", "naive_mse", "naive_mae", "rel_mse", "rel_mae"])
    for si, sr in enumerate(report["specs"]):
        for r in sr["rows"]:
            w.writerow([si, sr["lookback"], sr["n_params"], sr["bytes"],
                        r["dataset"], r["horizon"],
                        f"{r['mse_std']:.6f}", f"{r['mae_std']:.6f}",
                        f"{r['naive_mse']:.6f}", f"{r['naive_mae']:.6f}",
                        f"{r['rel_mse']:.6f}", f"{r['rel_mae']:.6f}"])
    return out.getvalue()


def cmd_tier_a(a) -> int:
    """CLI entry point for `ants tier-a`."""
    _refuse_non_val(a.split)

    specs = []
    if a.spec:
        for p in a.spec:
            specs.append(json.loads(Path(p).read_text()))
    if a.trial:
        # load specs from the ledger
        from .ledger import Ledger
        from .cli import _resolve_workdir
        _resolve_workdir(a)
        wd = Path(a.workdir).resolve()
        try:
            led = Ledger.open_readonly(wd / "ledger.sqlite")
        except OSError as e:
            print(f"ants tier-a: {e}", file=sys.stderr)
            return 1
        try:
            for tid in a.trial:
                row = led.conn.execute("SELECT gym,params FROM trials WHERE id=?", (int(tid),)).fetchone()
                if row is None:
                    print(f"ants tier-a: trial {tid} not found", file=sys.stderr)
                    return 1
                if row[0] != "tinyts":
                    print(f"ants tier-a: trial {tid} is not a TinyTS specification", file=sys.stderr)
                    return 1
                specs.append(json.loads(row[1]))
        finally:
            led.close()

    if not specs:
        print("ants tier-a: no specs given (use --spec FILE or --trial ID)", file=sys.stderr)
        return 1

    lb = getattr(a, "lookback", None)
    report = tier_a_report(specs, a.split, lookback_override=lb)

    if a.csv:
        Path(a.csv).write_text(format_csv(report))

    if a.json:
        text = json.dumps(report, indent=1, default=str)
    elif a.csv:
        text = format_csv(report)
    else:
        text = format_markdown(report)

    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    return 0
