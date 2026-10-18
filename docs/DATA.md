# Data

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
8. Tests follow the install: `tests/test_data.py` and `tests/test_real_eval.py` skip, naming the missing files, until the tier they read is fetched — the dev tier keeps the rest of the suite green, the full tier (`scripts/fetch_data.sh all`) runs all of it.

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
| name | family | role | shape | split | ref period | horizons | licence |
|---|---|---|---|---|---|---|---|
| etth1 | ETT | dev | 14400 x 7 | 8640/2880/2880 | 24 | [96, 192, 336, 720] | CC BY 4.0 (as stated by the repository maintainers; verify b |
| etth2 | ETT | confirm | 14400 x 7 | 8640/2880/2880 | 24 | [96, 192, 336, 720] | CC BY 4.0 (as above) |
| ettm1 | ETT | dev | 57600 x 7 | 34560/11520/11520 | 96 | [96, 192, 336, 720] | CC BY 4.0 (as above) |
| ettm2 | ETT | confirm | 57600 x 7 | 34560/11520/11520 | 96 | [96, 192, 336, 720] | CC BY 4.0 (as above) |
| weather | Weather | dev | 52696 x 21 | 36887/5269/10540 | 144 | [96, 192, 336, 720] | CC BY 4.0 |
| weather_clean | Weather | stress | 52696 x 21 | 36887/5269/10540 | 144 | [96, 192, 336, 720] | CC BY 4.0 |
| exchange | FX | dev | 7588 x 8 | 5311/758/1519 | 1 | [96, 192, 336, 720] | see source repository (LSTNet, Lai et al. 2018) |
| ili | Epidemic | confirm | 966 x 7 | 676/96/194 | 52 | [24, 36, 48, 60] | US government public data (CDC); community mirror used for t |
| solar | Solar | confirm | 52560 x 137 | 31536/10512/10512 | 144 | [96, 192, 336, 720] | see source repository (LSTNet) |
| electricity | ECL | sealed | 26304 x 321 | 18412/2630/5262 | 24 | [96, 192, 336, 720] | UCI ElectricityLoadDiagrams (CC BY 4.0) via LSTNet |
| traffic | Traffic | sealed | 17544 x 862 | 12280/1754/3510 | 24 | [96, 192, 336, 720] | California DOT PeMS public data via LSTNet |
| airquality | AirQuality | confirm | 9357 x 13 | 6549/935/1873 | 24 | [24, 48, 96, 168] | CC BY 4.0 (UCI) |
| bike | Mobility | confirm | 17379 x 7 | 12165/1737/3477 | 24 | [24, 48, 96, 168] | CC BY 4.0 (UCI) |
| appliances | HomeEnergy | confirm | 19735 x 28 | 13814/1973/3948 | 144 | [144, 288, 576] | CC BY 4.0 (UCI) |
| household | HouseholdPower | sealed | 34587 x 7 | 24210/3458/6919 | 24 | [24, 96, 336, 720] | CC BY 4.0 (UCI) |
| metro | Metro | sealed | 40575 x 5 | 28402/4057/8116 | 24 | [24, 96, 336] | CC BY 4.0 (UCI) |
| sunspot_daily | Sunspots | sealed | 76244 x 1 | 53370/7624/15250 | 1 | [96, 192, 336, 720] | CC BY-NC 4.0 (SILSO/SIDC, Royal Observatory of Belgium) - no |
| sunspot_monthly | Sunspots | sealed | 3333 x 1 | 2333/333/667 | 1 | [12, 24, 48, 96] | CC BY-NC 4.0 (SILSO/SIDC) |
| m4_hourly | M4 | confirm | 700 x 414 | 489/70/141 | 24 | [48, 96] | see source repository (M4 competition data) |
| cpi | Macro | stress | 956 x 1 | 669/95/192 | 12 | [12, 24, 36] | FRED terms; underlying data U.S. BLS (public domain) |
| melbourne | Climate | confirm | 3650 x 1 | 2555/365/730 | 365 | [30, 90, 180] | see source; small classic dataset |
| airline | Classic | stress | 144 x 1 | 100/14/30 | 12 | [12, 24] | see source; classic public series |
| river_saugeen | River | confirm | 20000 x 1 | 14000/2000/4000 | 7 | [14, 30, 90, 180] | not stated in the collection README (autogluon/chronos_datas |
| pedestrian | Pedestrian | confirm | 20000 x 16 | 14000/2000/4000 | 24 | [24, 96, 168, 336] | not stated in the collection README (autogluon/chronos_datas |
| air_kdd | AirKDD | confirm | 10920 x 16 | 7643/1092/2185 | 24 | [24, 96, 168, 336] | not stated in the collection README (autogluon/chronos_datas |
| rideshare | Rideshare | confirm | 541 x 16 | 378/54/109 | 24 | [24, 48, 96] | not stated in the collection README (autogluon/chronos_datas |
| uber_hourly | Uber | confirm | 4344 x 16 | 3040/434/870 | 24 | [24, 96, 168] | not stated in the collection README (autogluon/chronos_datas |
| taxi_30min | Taxi | confirm | 1488 x 16 | 1041/148/299 | 48 | [48, 96, 336] | not stated in the collection README (autogluon/chronos_datas |
| tourism_monthly | Tourism | confirm | 333 x 16 | 233/33/67 | 12 | [12, 18, 24] | not stated in the collection README (autogluon/chronos_datas |
| hospital | Hospital | confirm | 84 x 16 | 58/8/18 | 12 | [6, 12] | not stated in the collection README (autogluon/chronos_datas |
| covid_deaths | Covid | confirm | 212 x 16 | 148/21/43 | 7 | [14, 28, 56] | not stated in the collection README (autogluon/chronos_datas |
| car_parts | CarParts | confirm | 51 x 16 | 35/5/11 | 12 | [3, 6] | not stated in the collection README (autogluon/chronos_datas |
| nn5_weekly | ATM | confirm | 113 x 16 | 79/11/23 | 1 | [4, 8, 13] | not stated in the collection README (autogluon/chronos_datas |
| fred_md | FREDMD | confirm | 728 x 16 | 509/72/147 | 12 | [12, 24] | not stated in the collection README (autogluon/chronos_datas |
| elec_weekly | ECL | sealed | 156 x 16 | 109/15/32 | 1 | [4, 8, 13] | not stated in the collection README (autogluon/chronos_datas |
| aus_electricity | AusElec | sealed | 20000 x 5 | 14000/2000/4000 | 48 | [48, 336, 672] | not stated in the collection README (autogluon/chronos_datas |
| wind_farms | Wind | sealed | 8784 x 16 | 6148/878/1758 | 24 | [24, 96, 168, 336] | not stated in the collection README (autogluon/chronos_datas |
| ercot | ERCOT | sealed | 20000 x 8 | 14000/2000/4000 | 24 | [24, 96, 168, 336] | not stated in the collection README (autogluon/chronos_datas |

95 synthetic entries (`syn_*`, role `stress`) are listed by `ants data list --all`.
