# contract

Task contract (`task.toml`) and the typed, conditional, layered search space.

**Interface**
- `load_contract(path)` -> `Contract`; `Space.validate` is strict (no clamping); `Space.key` collapses dormant parameters so equivalent specs dedupe.
- `Param.when` (conditional activity), `layer` (lifecycle layer), `role='skeleton'` (defines a niche), `default`.
- `Param.sample/neighbor/from_unit` always return values inside the closed interval (regression-tested: exp(log(hi)) can exceed hi).
- `check_command`, `render_command`, `code_fingerprint(paths)`, `load_static_cost("antslab.module:function")`, `SpecError`.

**Invariants**
- A contract is validated completely at load time (`SpecError` naming the field, a file over 1 MiB is refused): finite bounded numbers, positive distinct sorted `caps` given together with `cap_metric` (a missing cap would silently remove the hard limit), declared stages only, parameter kinds/bounds/choices/defaults/`when` (a non-empty list of values; a bare string would match as a substring), at most 128 parameters, a small discrete skeleton (at most 100 000 cells, it is swept exhaustively), `static_cost` / `static_mem` only from `antslab.*`, `env_passthrough` never forwards secrets (`KEY`, `TOKEN`, `SECRET`...) or loader/interpreter variables (`LD_*`, `DYLD_*`, `PYTHON*`, `PATH`, `HOME`...), `code_paths` entries must exist when the fingerprint is taken.
- `commands` are argv lists; placeholders are only `{python} {stage} {spec} {out} {seed} {attempt}` (no attribute/index access, conversions or format specs). Templates are vetted at load time; `campaign` and `gate` then call `str.format` on them directly (they do not go through `render_command`).
- `Param.validate` is type-exact for `cat` (True is not 1) and never raises anything but `SpecError`, whatever the input (huge integers included).
- LLM output enters the system only through `Space.validate`.
- `code_fingerprint` hashes the bytes (and base names) of every file in `code_paths` (a directory contributes its `*.py` files); it is part of the epoch and is frozen with a candidate.

**Notes / failure behaviour**
- A bad contract fails at load time, not mid-run.
- Not validated: unknown keys in `[task]`, `[budget]`, `[objective]`, `[cascade]`, `[disk]`, `[record]` are ignored, so a typo silently takes the default. The `[sealed]` section and `[budget] unseal_lifetime` are read by `gate` straight from the raw TOML, not by `load_contract`.
- `{python.__class__}`-style commands, a `when` given as a string, `logfloat` with `low <= 0`, NaN/inf bounds and caps without `cap_metric` are all load errors.
