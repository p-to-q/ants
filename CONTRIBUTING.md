# Contributing

ants accepts small, reviewable changes that keep the evaluator out of reach of the things it evaluates.

1. Read [docs/principles.md](docs/principles.md) and [AGENTS.md](AGENTS.md).
2. Install: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[gyms,search,dev]"`.
3. Test: `.venv/bin/python -m pytest tests -q -m "not slow"`; run the full suite (including `-m slow`) before touching the ledger, runner or campaign.
4. Add a test for every new failure path, and a negative control for every new metric.
5. Update the module page, [docs/surfaces.md](docs/surfaces.md) and, for durable changes, [docs/changes.md](docs/changes.md).
6. Claims about performance state the budget, the seeds and the comparison arm. "Better" without those is not accepted.
7. Do not add datasets, secrets or private notes. New datasets need a catalog entry, a licence and a manifest hash ([docs/DATA.md](docs/DATA.md)).

By contributing you agree your contribution is licensed under Apache-2.0. Security issues: see SECURITY.md.
