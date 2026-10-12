"""Regenerate docs/DATA.md from data/catalog.toml + data/processed/PROCESSED.json (so the tables can never drift from the protocol)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from antslab.data import Catalog

HEAD = """# Data

The data layer is **protocol**, not convenience: which dataset plays which role is fixed in `data/catalog.toml`. No agent, operator or LLM
may choose datasets: a spec cannot name one, and the stage datasets are fixed in the gym.

**What a change does.** A new **epoch** starts only through the campaign's identity (evaluator code files + contract + `data/MANIFEST.sha256`, see
[E0](evaluation/E0-integrity.md)): editing a role or split in `catalog.toml` starts one only for contracts that list `catalog.toml` /
`catalog_synth.toml` in `code_paths` (`contracts/tinyts_real.toml` does, the two synthetic contracts do not); changing a raw file does so only if its
hash line in `MANIFEST.sha256` changes. Raw files are not re-hashed while a campaign runs (`ants data verify` does it), `data/processed/*.npz` is not
part of the identity, and the epoch is computed once per process. `Catalog.protocol_hash()` (catalogs + manifest + parser version) exists and
`gate.seal` checks it when a freeze recorded it, but the campaign does not use it. The campaign's own search state (history, seen specs, frontier) is
filtered to the current epoch; `ants best`, the board, the report and the LLM proposers' prompts read all epochs.

**How roles are enforced.** `Catalog.load` refuses datasets outside the process's allowed roles when `ANTS_ALLOWED_ROLES` is set or a
`data.role_policy` block is active: `ants run` starts every gym with `dev`, `gate.seal` runs the final command with `dev,confirm,sealed`. With no
policy (tools, tests, `ants data`, a gym started by hand) every role loads, so the protection is the environment variable plus discipline, not a wall.

## Roles
| role | used by | rule |
|---|---|---|
| `dev` | the search (cascade stages S0-S2) | VALIDATION windows only; rolling-origin folds inside train+val |
| `confirm` | E2 confirmation (paired tests, family support) | never touched by the search loop |
| `sealed` | E7 claim | whole source families untouched until the one-shot final evaluation |
| `stress` | gates and diagnostics (E0/E3/E6) | never mixed into the headline score; includes the synthetic suite |

A **family** is an independent source: correlated datasets (ETTh1/h2/m1/m2; the weekly and the raw electricity clients) share a family and are
weighted once. Sealed families are disjoint from dev/confirm families (tested). A synthetic surrogate of a sealed or confirm series (`syn_*` with a `synth_base`) keeps role `stress` in the catalog but inherits the base's role for access (`Catalog.access_role`).

## Rules for any agent working with data
1. Standardise with TRAIN statistics only; targets are never clipped; inputs may be (deployable pre-processing).
2. Training windows' targets end before the evaluation block starts. Large datasets use a deterministic capped sample of (channel, start) pairs.
3. Headline = family-weighted geometric mean of MSE(model) / MSE(seasonal naive at the dataset's own period). Published tables are reference only; baselines are re-run here.
4. Short series shrink the look-back (`n_train - H) / 3`) and skip horizons that do not fit; skips are reported, never silently ignored.
5. Single-split validation is unreliable on some sets (ETTh1: validation MSE is about twice the test MSE and the model ranking flips), so selection uses several folds and several families.
6. Never use `stress` or sealed data for tuning. Never look at test windows of a candidate before it is frozen.
7. Adding a dataset: add a catalog entry and its raw hash to the manifest, rebuild with `ants data build`, run the tests; expect a new epoch for the real-data contract (no separate qualification script exists).

## Unified processed format (`data/processed/<name>.npz`)
`values` float32 (T, C) (storage precision; relative error about 6e-8) and `meta` (JSON): name, family, role, ref_period, horizons, split `[train, val, test]`,
source, licence, note, parser version, SHA-256 of every raw file (or the generator family/seed for synthetic data). `data/PROCESSED.json` holds the hash of each file.
Build everything with `ants data build`; verify raw files with `ants data verify`; browse with `ants data list`.

## Known data-quality facts (found while building this)
* **Weather** (Max Planck, Jena 2020, 52,696 x 21): value-identical to the community copy used by published benchmarks; it contains **81 sentinel values (-9999)** that the benchmark version keeps. `weather` is benchmark-faithful; `weather_clean` replaces them (stress).
* **ETT**: the standard protocol uses only the first 20 months (ETTh 14,400 rows, ETTm 57,600) with a 12/4/4-month split; the files contain more.
* **Air Quality**: `-200` sentinels forward-filled. **Household power**: 1-minute data aggregated to hourly means. **Metro traffic**: duplicate timestamps collapsed; gaps NOT re-sampled.
* **Sunspots, CPI** are live series (still updating in 2026): the snapshot is pinned by hash; re-downloading later gives a different file and `data verify` will say so.
* **Chronos-collection sets** (`river_saugeen` ... `ercot`): series are independent and NOT time-aligned across channels; licences are not stated in the collection README. Raw data are not redistributed by this repository.
* **Appliances** contains two random-variable channels (`rv1`, `rv2`): built-in negative controls.
* ILI, hospital, car parts, NN5, tourism, airline are tiny: results on them are weak evidence by construction.

## Synthetic suite (`stress`)
Every generator is a pure function of (family, seed), regenerated byte-identically, and carries its truth where known.
* **mechanism** (generating logic differs): sines+AR, SNR ladder (100/10/1/0.1, known oracle MSE floor), regime switching, Lorenz, Mackey-Glass, Rossler, Van der Pol, Lotka-Volterra, Henon+logistic maps, calendar/holiday effects, weekday-gated cycles, intermittent demand, Hawkes bursts, heavy tails, long memory, chirps, cross-channel coupling, sawtooth/square waves, ARIMA, SARIMA, structural state-space, GARCH, Ornstein-Uhlenbeck, random echo-state networks, random nonlinear AR, kernel-composition (Chronos-KernelSynth style) and **hybrid** mixtures (channels built from different random compositions, so no single mechanism).
* **negative controls** (no model may show skill; skill means leakage): white noise, random walk, and time-**shuffled** versions of real series.
* **extreme**: huge / tiny scale, constant channels, a series that is one number, missing blocks, outlier bursts, end-of-series level shift, very short, quantised, exponential growth, random daily sign flips, piecewise-constant steps, saturated (pinned at bounds), on/off machine states, monotone staircase.
* **reality-calibrated surrogates** of real data: phase-randomised (same spectrum), block-bootstrap, AR(24)-simulation (real linear dynamics only), shuffled (same marginal, no time).
Tests check known answers: no model beats the SNR noise floor, negative controls show no skill through the real pipeline, scale extremes vanish after standardisation, surrogates keep what they claim.

## Datasets
"""

cat = Catalog(ROOT / "data" / "catalog.toml")
proc = {r["name"]: r for r in json.loads((ROOT / "data" / "processed" / "PROCESSED.json").read_text())} if (ROOT / "data/processed/PROCESSED.json").exists() else {}
rows = ["| name | family | role | shape | split | ref period | horizons | licence |", "|---|---|---|---|---|---|---|---|"]
for n in cat.names():
    if n.startswith("syn_"):
        continue
    m = cat.meta[n]
    try:
        s = cat.load(n); shape = f"{s.values.shape[0]} x {s.values.shape[1]}"; sp = f"{s.n_train}/{s.n_val}/{s.n_test}"
    except Exception:
        shape, sp = "(not built)", ""
    rows.append(f"| {n} | {m['family']} | {m['role']} | {shape} | {sp} | {m['ref_period']} | {m.get('horizons', cat.defaults.get('horizons'))} | {m.get('license','')[:60]} |")
syn = [n for n in cat.names() if n.startswith("syn_")]
tail = f"\n{len(syn)} synthetic entries (`syn_*`, role `stress`) are listed by `ants data list --all`.\n"
(ROOT / "docs" / "DATA.md").write_text(HEAD + "\n".join(rows) + "\n" + tail)
print("wrote docs/DATA.md:", len(rows) - 2, "real datasets +", len(syn), "synthetic")
