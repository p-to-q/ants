# Changes

Append-only, newest first, only durable changes. Three lines each. `Forecloses:` names the door the change shuts.

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
