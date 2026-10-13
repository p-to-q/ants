# Changes

Append-only, newest first, only durable changes. Three lines each. `Forecloses:` names the door the change shuts.

## 2026-10-13 · Review of the system layer: 11 findings fixed, update check, 0.2.0
Changed: an independent reviewer broke the first version of backup, restore, service, fetch and the versioned ledger. Fixed: backup anchors are readable by `verify_anchor_file`; restore moves a stale write-ahead log aside, never overwrites an earlier moved-aside ledger, refuses a snapshot older than the ledger it would replace or taken from another ledger (`--allow-different`) and writes a `restored` event; backups get exclusive names, leave nothing behind on failure, skip junk sidecars and are ordered by creation time; default backup directories include a hash of the workdir; the unit file escapes `%`, `$` and backslashes; a heartbeat thread covers the long blocking model calls (dream, refill) for at most 15 minutes so the 300 s watchdog does not restart a healthy loop; `ants onboard` keeps earlier choices and a corrupt `config.toml` is reported; a ledger from a newer ants is refused before any write, and the first migration keeps a full copy; `ants sql` caps value sizes; dataset fetching refuses unsafe names and plain-http redirects and deletes partial downloads; LLM spend is capped per rolling day (`ANTS_LLM_MAX_CALLS_PER_DAY`, default 1500). Added `ants update` and a quiet once-a-day check; units for janitor, health and backup are now per arm (`@<arm>`), and the campaign unit is `Type=notify` with a watchdog; version 0.2.0.
Why: a resident system is judged by what it does on the day something goes wrong; each of these was reproduced, not guessed.
Forecloses: restoring an older snapshot over a newer ledger without saying so; an update that happens inside a running daemon; a watchdog shorter than the longest blocking call; units that name one arm.

## 2026-10-13 · A resident system: onboard, service, backup, sql, versioned ledger
Changed: `ants onboard` (home directory, datasets from `data/sources.toml` checked against the pinned manifest, `config.toml`), `ants service install` (systemd user unit with `Type=notify` and a watchdog heartbeat sent by the loop itself, launchd agent, daily backup timer), `ants backup/backups/restore` (SQLite online backup, kept only if integrity and chain verify, sidecar and `anchors.jsonl`, restore moves the old file aside), `ants sql` (read-only authorizer, time and row caps), `ants data fetch`, plain `ants` offers setup on the first run; the ledger carries `PRAGMA user_version` with forward-only migrations and refuses a file written by a newer ants; `ants doctor` adds integrity, WAL size, network filesystem and backup age.
Why: one command should wake it, keep it alive and recoverable, and make its data readable without a second tool. The pattern is the one OpenClaw, Pi, NanoClaw, TinyClaw and nanobot share (home directory plus one config, a service installer, a status probe); none of them verifies a backup or versions its store, which is where a long-lived ledger needs more.
Forecloses: copying the live ledger file as a backup; a service that restarts a hung loop only by timeout; a ledger opened by an older ants than wrote it; downloading datasets without a pinned hash or over plain http.

## 2026-10-13 · Name, one line, and what the README no longer stresses
Changed: ants now reads as **Autonomous Navigation of Tiny-model Space**; the one line is "A small autonomous research loop: it starts on one cheap machine and scales with the compute you give it." The slogan "Agents Never Touch Scoring", "CPU-only", "evidence ledger" and "hard parameter budgets" are gone from the name, the banner, the repository description and the first paragraph; the architecture figure no longer draws the ledger. Small and cheap describe where ants starts, not what it is limited to.
Why: the ledger is a necessary part, not a selling point; a parameter cap describes the first domain, not the project; nano-style projects are defined by starting small and growing.
Forecloses: describing ants by its cap or its ledger; using the old slogan as a name or tagline.

## 2026-10-13 · Red team: sealed credits, registry proof, roles, anchors
Changed: sealed looks are scoped per (gym, epoch, sealed set) with a lifetime cap, one look per freeze hash, counted from the hash chain as well as the table, and closed by exactly one result; `Registry` climbs only with recorded evidence (`confirm`, `challenge` on identical windows with online FDR, a sealed result for the champion); `decide_promotion` fails closed on cherry-picked or malformed evidence; roles are enforced in `Catalog.load` when `ANTS_ALLOWED_ROLES` is set (the campaign sets `dev`, `gate.seal` sets `dev,confirm,sealed`) and surrogates inherit the role of sealed/confirm bases; catalog edits are validated; ledger anchors detect tail truncation and rewrites; `frontier`, `seen_keys` and `freeze_best` use the current epoch only; `gmre` and the paired statistics raise on malformed input.
Why: the freeze-then-seal design was sound but its bookkeeping could be bypassed (refunded credits, forged results, bare `transition()`, a challenger that dropped the families it loses on, an unchecked sealed dataset argument).
Forecloses: counting sealed looks from a mutable table alone; promoting a model without recorded evidence; treating the role firewall as closed: it is opt-in per process, the default is open, and the registry, `gate.seal` and anchors have no production caller.

## 2026-10-13 · Security hardening: AST patch gate, transport, contract, deploy
Changed: the maintainer gate parses code (AST before/after, imports allow-list, frozen I/O code) instead of matching text, and its sandbox runs with a minimal environment, rlimits and a nonce-protected golden replay; LLM and Jev transport is https-only with no redirects and bounded, strict reads; `extract_json` and `Space.validate` cannot hang or raise foreign errors; the contract loader validates every field; terminal output goes through `textsafe.clean_text`; the board escapes everything, carries a CSP and `ants serve` uses a loopback-only, Host-checked server; packaging scans the staging tree and the tarball for secrets; the units were renamed to `ants*`, no longer load `llm.env` into the environment and add sandboxing options.
Why: reproduced bypasses of the text deny-list, an API key forwarded on redirects, unbounded reads, unsanitised terminal and HTML output, and an installer that referenced files that did not exist.
Forecloses: a raw-text deny-list as the maintainer's only gate; redirects on calls that carry a key; describing the AST gate as a sandbox (it is static analysis; the TinyTS evaluator is still on the maintainer's allow list); claiming the units are proven (they were never started under systemd).

## 2026-10-13 · Chaos: crash, clock, disk and shutdown paths
Changed: one `ants run` per workdir (`flock`, second instance exits 3); SIGTERM/SIGINT stop a running trial within seconds (0.25 s polling, SIGTERM, 3 s grace, SIGKILL) and requeue it; a failed `COMMIT` no longer wedges the connection; transient SQLite/OS errors are retried instead of killing the daemon; event timestamps never go backwards and the breaker and timeouts use the monotonic clock; `metrics.json` is read without blocking, size-capped and must be a flat dict of finite numbers; escapees that left the process group are killed by a per-trial token; archive retries mismatches and retires vanished files; the janitor re-checks every delete condition on a fresh row; relative `--workdir` works; the epoch is stamped at claim.
Why: reproduced double evaluation by two daemons, lost evaluations on a locked database, a hung supervisor on a FIFO named `metrics.json`, a stuck archive window and deletions based on a stale plan.
Forecloses: assuming one process per workdir without the lock; reading `metrics.json` with `read_bytes()`; trusting a plan older than the delete call. Still open: finish and promotion are not atomic, and a daemon `kill -9` outside systemd leaves its gym running.

## 2026-10-13 · Independent audit: 36 findings, release blocked until fixed
Changed: an isolated reviewer audited a snapshot (4 blocker, 9 high, 19 medium, 4 low). Fixed so far: history readers saw only the first 20 000 trials; malformed model JSON could crash `dream` and leak proposer slots; "standard library only" now describes the search loop (statistics, data and gyms need numpy) and a test enforces it; no LICENSE; package name collision. In progress: systemd file-size limit on the ledger, relative `--workdir`, epoch coverage, installer deleting state, maintainer patch gate, janitor delete-time checks.
Why: the thesis (agents never touch scoring) is only as strong as the weakest unchecked claim, and several README claims were not supported by code.
Forecloses: shipping before the blockers are closed; describing the core as "stdlib only" without qualification.

## 2026-10-13 · Named ants; Apache-2.0
Changed: the project is **ants**, read as **Agents Never Touch Scoring**; package `ants-lab`, module `antslab`, command `ants`; code licensed Apache-2.0.
Why: a short name for a thing that works by many small agents leaving a trail; Apache-2.0 is the p-to-q default and gives an explicit patent grant.
Forecloses: publishing under the bare name `ants` on npm or PyPI (taken); a copyleft licence for the code.

## 2026-10-13 · Private design thinking stays out of the repository
Changed: design notes, literature sweeps and idea logs live in `internal/` and are excluded by `.gitignore` and `scripts/pack_public.sh`.
Why: the public record should be code, contracts, evidence and the principles that are enforced; the rest is working memory.
Forecloses: citing private notes as justification inside public docs.

## 2026-10-12 · Hard cap as a constraint, rolling-origin validation on real data
Changed: parameter caps gate candidates instead of entering the objective; stages S1/S2 use expanding-window folds across several families.
Why: a single fixed validation segment reversed model rankings on ETTh1; a penalty term lets over-cap models lead.
Forecloses: a scalar "accuracy minus lambda × size" headline.
