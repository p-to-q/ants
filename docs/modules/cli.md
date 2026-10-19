# cli

`archive index` builds an immutable round summary from a verified bundle;
`archive list` reads its cache. The default snapshot kind omits result metrics.
See [archive_index](archive_index.md) for flags and safety boundaries.
Bare `ants` uses the viewer's workdir discovery for its first-run gate, including
series directories. An existing history index also bypasses onboarding after
live ledgers move away. A damaged index is shown as a repairable read-only error,
not a reason to download datasets. The gate never opens or modifies a ledger.

Command line (`ants`, entry `antslab.cli:main`). Flag-by-flag reference: [docs/cli.md](../cli.md).

**Interface**
- Commands (see `docs/cli.md` for the authoritative table). Daemon: `run`. Read/report: `status`, `top`, `best`, `show`, `log`, `doctor`, `export`, `board`, `serve`. Control: `hint`, `pause`, `resume`, `janitor`. Data: `data list|verify|build`. Interactive: `shell` (also plain `ants`).
- Common flags: `--contract`, `--workdir`, `--arm`, `--seed`, `--json`. Missing ones are resolved in this order: flag, `ANTS_CONTRACT` / `ANTS_WORKDIR` / `ANTS_ARM`, installed service, newest `runs/*/ledger.sqlite`, defaults (arm `portfolio`); the choice is printed to stderr unless `--json`.
- `--contract` and `--workdir` are resolved to absolute paths in `run` and in every command that opens the ledger.
- Exit codes: `run` 0 after a clean stop, **3** if another `ants run` holds `<workdir>/ants.lock` (the message names the holder pid); `status` 2 if the hash chain is broken; `doctor` 2 if any check is `FAIL`; `show` 1 if the trial is unknown; `data verify` 2 on drift; an unhandled error exits 1 (a traceback; the daemon does this after five consecutive non-transient step errors).
- No command freezes a candidate, runs `gate.seal`, writes an anchor, drives the `Registry`, `judge`, `maintainer` or `report`. They are library code only.

**Invariants**
- SIGTERM/SIGINT: a running trial is noticed within 0.25 s, its process group is stopped (SIGTERM, 3 s grace, SIGKILL) and the trial is requeued (reason `shutdown`); a blocking LLM call is abandoned immediately; the process exits 0. If non-daemon LLM worker threads remain it leaves with `os._exit(0)` so systemd does not have to SIGKILL it.
- The instance lock is taken before the ledger is opened.
- Every string that did not originate in our code (hints, sources, gym stderr, LLM text) goes through `textsafe.clean_text` before it is printed ([ui](ui.md)). `export` neutralises spreadsheet formulas (`=`, `+`, `-`, `@`, tab, CR at the start of a text cell get a leading `'`).
- `serve` binds loopback only through `board.make_server` and regenerates the board every `--every` seconds in a daemon thread.
- The read commands (`status`, `top`, `best`, `show`, `log`, `doctor`) do not change the ledger. `hint` appends an event (flattened to one line), `pause`/`resume` toggle the `PAUSE` file; `export`, `board`, `serve` and `data build` write files; `janitor` archives and deletes under the rules in [janitor](janitor.md).

**Notes / failure behaviour**
- `ants run` does not call the maintainer, judge, registry, gate, report; arms using `dream` run it every `--dream-every` finished trials (default 100).
- LLM endpoints for the `llm*` and `portfolio_llm` arms come from `--llm-env`, else the first existing of `~/.config/ants/llm.env`, `/etc/ants/llm.env`; the file must be mode 600.
- `janitor` passes no keep list to the cleaner (see [janitor](janitor.md)).
