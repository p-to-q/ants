# maintainer

Lets an LLM draft small repairs of gym code under a strict gate, with rollback. Library code: not called by `ants run` or any CLI command.

**Interface**
- `check_patch(patch, root, policy)` -> list of reasons ([] = acceptable): normalised relative paths, no symlinks, exact case, allow glob where `*` never crosses `/`, deny list, limits (2 files, 8 edits, ~40 changed lines, 4000 characters per old/new text, 1 MiB per file), uniqueness of `old` checked in application order, no control/bidi/NUL/lone-CR characters, the forbidden-token list, and the **AST gate**.
- `ast_gate(before, after)`: parses the file before and after and rejects the patch if it adds an import outside `SAFE_IMPORTS` (relative imports only of `data`, `metrics`, `synth_suite`), a dynamic-execution or I/O builtin or attribute (`DANGEROUS_NAMES`, `DANGEROUS_ATTRS`), dunder access or an attribute-walking format string; and it freezes every function or module-level statement that already performs I/O or dynamic execution, the names that feed it, and its call sites. A file that no longer parses is rejected.
- `Maintainer(ledger, root, backup_dir, policy, test_cmd).attempt(patch, module, func, specs, repair_specs)` (sandbox copy -> tests -> golden replay -> repair check -> re-validate -> apply), `rollback(pid)`, `should_roll_back(since_ts)`, `draft_repair(client, failure_summary, files)` (LLM output treated as untrusted).
- `Policy.allow` defaults to `src/antslab/gyms/*.py`; `Policy.deny` lists ledger, gate, runner, contract, campaign, registry, metrics, judge, maintainer, llm, cli, ui, board, `tests/*`, `contracts/*`, `deploy/*`, `scripts/*`, `requirements/*`, `*.toml`, `*.env`.

**Invariants**
- The patched code is untrusted: it runs in a copy, with a minimal environment (no API keys), rlimits (CPU, 64 MB files, no core), its own process group and a hard timeout; golden-replay results are accepted only on a per-run nonce line and the child exits with `os._exit`. A missing or NaN metric counts as drift.
- Apply/rollback write atomically and byte-for-byte (CRLF, any encoding); backups have a manifest with SHA-256 per file and are verified before a restore; patch ids are 16 hex digits.
- A repair must not change any metric that was already correct (golden replay, relative tolerance 1e-9) and must make the failing spec succeed.
- One applied patch per day by default; every step is a ledger event (`patch_proposed|rejected|applied|rolled_back`).

**Notes / failure behaviour**
- **What is NOT protected:** the evaluator itself. `src/antslab/gyms/tinyts.py` matches the allow list, so a patch to the TinyTS evaluator (training and scoring code) is allowed unless it touches I/O code or adds a dangerous construct. Only the files in the deny list are out of reach, and `data.py` is outside the default allow list only because the allow list is narrow, not because it is denied. The prompt sent to the model says "never touch evaluation semantics"; that is a request, not a gate. A patch to a file in the contract's `code_paths` starts a new epoch, but a running daemon computes its epoch once at start-up (see [campaign](campaign.md)).
- The AST gate is best-effort static analysis, NOT a sandbox (see [SECURITY.md](../../SECURITY.md)). Patched compute code (not I/O code) can still be arbitrary numerics; I/O functions of a gym (for example `main`, `load_ett` of tinyts) cannot be repaired by design.
- The raw-token list (`Policy.forbidden`: `subprocess`, `socket`, `open(` ...) still runs before the AST gate, as an additional layer.
- A patch that changes an I/O-free numeric function passes the gate; whether it is correct is decided by the tests and the golden replay only. A model reply that does not parse makes `draft_repair` return None.
