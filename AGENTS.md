# ants — notes for coding agents

A small autonomous research loop: it starts on one cheap machine and scales with the compute you give it. Typed search space, fixed evaluator, registry. Python 3.12, standard-library core, numpy only in gyms. Read [docs/principles.md](docs/principles.md) first; it is short.

## Read route (read only what your change needs)
* Touching the ledger, runner, campaign, janitor → `docs/evaluation/E0-integrity.md` and the module page in `docs/modules/`.
* Touching metrics, registry, gate, data roles → `docs/evaluation/E1`–`E2`, `E7`, `docs/DATA.md`.
* Touching a gym or contract → `docs/modules/gyms-tinyts.md`, `docs/architecture.md` ("Extending").
* Touching search operators → `docs/modules/portfolio.md`, `proposers.md`, and [docs/open.md](docs/open.md).
* Unsure what is real → [docs/surfaces.md](docs/surfaces.md).

## Invariants (do not weaken; add a test for any new failure path)
* **E0:** fencing token, hash chain, strict `Space.validate`, verified-before-delete, freeze-then-seal.
* A hard cap is a constraint, never an objective term. Cheap deterministic stages keep ledger rows only.
* LLM output is untrusted data: it enters only through `Space.validate`. Never log or commit API keys; env files are `chmod 600`.
* Compare arms at equal evaluation budgets and report medians over seeds, not single runs.
* Private design notes live in `internal/` and are never published (`scripts/pack_public.sh` enforces it).

## Change discipline
* Run `.venv/bin/python -m pytest tests -q -m "not slow"` before and after; the chaos tests are `-m slow`.
* One MD per module in `docs/modules/`; update it, [docs/surfaces.md](docs/surfaces.md) and [docs/changes.md](docs/changes.md) (`Changed / Why / Forecloses`) in the same change when behaviour or maturity moves.
* Conventional Commits with a domain scope (`fix(ledger):`, `feat(portfolio):`). Do not commit, push or publish unless asked.

## Stop report (end every task with these five fields)
`Status / Scope / Validation / Risks / Next`. A check that was not run is written `Not run` with the reason, never claimed as passing.

## Using ants from another agent or script
Every read command has `--json` and meaningful exit codes (`doctor` exits 2 on FAIL; `show` exits 1 if the trial is unknown):
`ants top --once --json` (whole state), `best --json` (leaderboard per cap), `show <trial|key> --json` (spec, stage metrics, lineage),
`log -n 50 --json`, `doctor --json`. Controls: `hint "<text>"` (shown to proposers; can never change a gate or budget), `pause` / `resume`.
`ants shell` is the interactive version. Do not write to the ledger directly.
