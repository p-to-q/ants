# data

Catalog-driven data layer: the dataset roles ARE the evaluation protocol.

**Interface**
- `Catalog(path)`: `names(role)` (declared role), `names_for(roles)` (access role, see below), `access_role(name)`, `require_roles(names, allowed)` (fail closed), `load(name)` -> `Series` (values, chronological split, family, ref_period, horizons), `build_processed()`, `verify_manifest(only_existing=)`, `protocol_hash()`.
- `role_policy(roles)` (context manager), `active_policy()`, `ROLES_ENV = "ANTS_ALLOWED_ROLES"`, `locate_catalog()` (`$ANTS_CATALOG`, `$ANTS_HOME/data`, upwards from cwd, source tree, `~/.ants/data`), `CatalogError`, `RoleError`.
- `sample_windows(data, lo, hi, L, H, cap, seed)`: deterministic capped sample of (channel, start) pairs whose TARGET lies in [lo, hi).
- Parsers for ETT csv, LSTNet gz, Max-Planck weather zip, UCI zips, SILSO, FRED, M4, Chronos parquet, synthetic suite and real-derived surrogates.

**Invariants**
- Roles (dev/confirm/sealed/stress) are fixed in `data/catalog.toml`; sealed families are disjoint from dev/confirm families (tested). The add-on `catalog_synth.toml` may only ADD `stress` entries and may not redefine one; an unknown role, a missing family or a dangling `synth_base` raises `CatalogError`.
- Role and family come from the catalog. A processed `.npz` whose baked-in role, family or parser version disagrees with the catalog is ignored and the raw file is parsed instead.
- A surrogate (`synth_real`: spectrum/marginal/dynamics of a real series) inherits `sealed` or `confirm` from its base (`access_role`); surrogates of dev data stay `stress`. Use `names_for("stress")`, not `names("stress")`, for stress sweeps.
- Role enforcement is a policy on `Catalog.load`: inside `role_policy(...)`, or when `ANTS_ALLOWED_ROLES` is set (comma list; an empty, garbled or unknown value denies everything), a dataset whose access role is not listed raises `RoleError`. `Campaign` sets `dev` for every search process; `gate.seal` sets `dev,confirm,sealed`.
- Standardise with TRAIN statistics only. ETT uses the first 20 months only (standard protocol).
- `load()` prefers `data/processed/*.npz` (unified format, float32 storage) and falls back to parsing raw files; both give the same `Series`.

**Notes / failure behaviour**
- **The default is open.** With no policy active (tools, tests, `ants data`, a gym started by hand, direct calls to `tinyts.evaluate_catalog(...)`) `load` serves every role. `tinyts` itself does not call `require_roles`; the search is kept off sealed data by the environment variable the campaign sets, by the fixed dataset names in `CAT_STAGES` and by `Space.validate` (a spec cannot name a dataset). A default-deny policy was not adopted because `tests/test_data.py` loads sealed series.
- Live series (sunspots, CPI) are pinned by SHA-256 in `data/MANIFEST.sha256`; `ants data verify` reports any drift. Nothing re-hashes raw files while a campaign runs.
- `protocol_hash()` = hash of `catalog.toml`, `catalog_synth.toml`, `MANIFEST.sha256` and the parser version. What starts a new epoch is decided by the campaign, which does not use this hash: see [campaign](campaign.md) and [docs/DATA.md](../DATA.md).
- `_CACHE` is process-wide and keyed by dataset name.
