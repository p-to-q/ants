# Surfaces

What actually runs, and how far to trust it. Updated in the same change that moves a surface between states.

Status values: `stable` (rely on it) · `experimental` (feedback wanted) · `internal` (not a contract) · `stub` (visible placeholder) · `archived` (receipts only).

"Library" in Notes means: covered by tests, but not reachable from `ants run` or any CLI command.

| Surface | Status | Evidence | Notes |
|---|---|---|---|
| Ledger (SQLite, hash chain, fencing token, epochs) | stable | `tests/test_ledger_gate.py`, `test_chaos_db.py`, `test_chaos_clock.py`, `test_redteam_chain.py`, kill -9 chaos test | chain anchors exist (`anchor`, `write_anchor`, `verify_against_anchor`) but nothing ships them off-box, so tail truncation and a consistent rewrite stay undetected in practice; the `trials` table is not audited against its events |
| Runner (subprocess, process-group kill, clean env, strict `metrics.json`) | stable | `tests/test_runner.py`, `test_chaos_runner.py` | grandchildren that `setsid` are found by a per-trial token and killed; the Linux `/proc` scan is tested only against a synthetic tree, macOS `ps` only with python children |
| Campaign (cascade, breaker, pre-filters, disk levels, graceful stop, transient-error back-off) | stable | `tests/test_campaign.py`, `test_chaos_signals.py`, `test_chaos_misc.py`, `-m slow` | single-instance lock exists (`flock` on `<workdir>/ants.lock`, second `ants run` exits 3); finish and promotion are not one transaction (a kill can lose a promotion); a stopped gym can outlive a `kill -9` of only the daemon outside systemd |
| Epochs (identity of evaluator code, contract, data manifest) | experimental | `tests/test_redteam_epoch.py` | covers only the files in the contract's `code_paths` (the real-data contract lists the evaluator, `metrics.py`, `data.py` and the catalogs; the synthetic contracts only `tinyts.py`); computed once per process; `Catalog.protocol_hash()` is not part of it; `ants best/top`, board, report, dream and some proposers read all epochs |
| Search ecology (random … QD, portfolio) | experimental | `tests/test_landscape.py`, `bench` | no claim that it beats random at equal budget yet |
| Role enforcement (`ANTS_ALLOWED_ROLES`, `data.role_policy`) | experimental | `tests/test_redteam_roles.py` | the campaign sets `dev` for every gym process; `gate.seal` sets `dev,confirm,sealed`; with no policy `Catalog.load` is open, `tinyts` does not call `require_roles`, and `tinyts --final` run by hand reads sealed data |
| Registry and promotion ladder | experimental | `tests/test_registry.py`, `test_redteam_registry.py` | library: production does not call the Registry; each rung needs recorded evidence (gates, windows-identical challenge with LORD++, sealed result); `tinyts` emits no per-window losses to feed it |
| Evidence gate (freeze, sealed credits, anchors) | experimental | `tests/test_ledger_gate.py`, `test_redteam_credits.py` | library: no command freezes or seals; credits are per (gym, epoch, sealed set) with a lifetime cap and only ratchet down; `contracts/tinyts_real.toml` declares no `[sealed]` set |
| TinyTS gym on real data | experimental | `contracts/tinyts_real.toml`, `tests/test_real_eval.py` | dev validation only under `ants run`; sealed data untouched by the search while the role policy is set |
| Synthetic suite and negative controls | experimental | `tests/test_synth_suite.py` | |
| LLM proposer (`llm`, `portfolio_llm`) and client | experimental | `tests/test_llm_proposers.py`, `test_security_llm.py` | benefit unmeasured; https only, no redirects, size-capped reads; keys never logged |
| Judge authority ladder | experimental | `tests/test_judge.py` | library, shadow mode only: not attached to the loop; `ProposalTriage` reorders and never filters |
| Maintainer patch gate | experimental | `tests/test_maintainer.py`, `test_security_maintainer.py` | AST based (imports allow-list, dangerous names, frozen I/O code), sandbox copy with minimal env and rlimits; static analysis, not a sandbox; library, not wired to the daemon; the TinyTS evaluator itself is on the allow list |
| Dream (ledger lessons for `*_dream` arms) | experimental | `tests/test_audit_regressions.py`, `test_llm_proposers.py` | LLM claims re-checked against statistics; failure-cluster text from gym errors is not cleaned |
| Report and number lint | experimental | `tests/test_report.py` | library; the lint matches numbers against any fact, not by context |
| Interactive shell and `--json` CLI | stable | `tests/test_ui.py`, `test_security_ui.py`, `test_cli_hardening.py` | 28 subcommands (`ants --help`); exit codes: `run` 3 if locked, `doctor` and `status` 2, `show` 1; `ants doctor` is the only health check |
| Board (read-only, 127.0.0.1) | experimental | `tests/test_board.py`, `test_security_ui.py` | escaped values, CSP, loopback-only server with Host check; no authentication; mixes epochs |
| Terminal interface (`ants`, `ants ui`, `ants home`) | experimental | `tests/test_tui.py` (every screen from a real toy campaign, read-only check, command line, a pty run of the curses loop) | read-only ledger connection; `hint`, `pause`, `resume` write through the CLI; looked at as HTML renderings and on a snapshot of the server's ledger, not yet by a person in a terminal over SSH |
| Agent operator (`agent`, `portfolio_agent`) | experimental | `tests/test_agent.py`; one real batch against a copy of a server ledger (6 tool calls, 6 valid proposals) | read-only tools over the ledger (`sql` with `events`, `unseals`, `llm_calls`, `artifacts`, `epochs` denied, `show`, `diff`, `best`, `failures`); every call a chained `agent_tool` event; proposals validated like any other; benefit unmeasured ([open.md](open.md) question 9) |
| Workspace export and `ants diff` | experimental | run on a copy of a server ledger | a snapshot, not a view: nothing reads it back |
| Onboard, dataset fetch (`ants onboard`, `ants data fetch`, `data/sources.toml`) | experimental | `tests/test_system.py`, dev tier fetched for real into an empty home | https only, resumable, verified against the pinned manifest; only the dev tier was downloaded for real; the other tiers' URLs are unchecked |
| Backup and restore (`ants backup`, `backups`, `restore`) | experimental | `tests/test_system.py` | verified snapshot, anchors, restore moves the old file aside; never run against a multi-day ledger or on the server |
| Service install and watchdog (`ants service`, `notify.py`) | experimental | `tests/test_system.py` (rendering, heartbeat socket) | units rendered and tested as text; the systemd unit has run on the server since 2026-10-13 (notify and watchdog worked); the launchd agent was never loaded |
| Read-only SQL (`ants sql`) | experimental | `tests/test_system.py` | authorizer plus `mode=ro`; a denied statement raises, rows and time are capped |
| Janitor and archive (local backend) | stable | `tests/test_janitor_archive.py`, `test_chaos_archive.py` | delete conditions re-checked on a fresh row at delete time; `ants janitor` passes no keep list; 200 permanently failing rows would starve the archive window |
| Archive to S3-compatible storage | stub | | interface only; never run against a service; no test |
| Artifact checks (E3, ONNX parity, latency) | stub | | planned |
| Hypothesis ledger | stub | | planned |
| systemd units and `deploy/remote_deploy.sh` | experimental | `tests/test_security_deploy.py` (static checks) | running on the target machine since 2026-10-13 (three arms; `systemd-analyze security` 2.9 OK): `ants@.service` (Type=notify, watchdog, restart always, exit 3 not restarted), `ants-janitor@.*`, `ants-health@.*`, `ants-backup@.*`, `ants-board.service` |
| Packaging scan (`scripts/scan_secrets.py`, `pack_public.sh`) | experimental | `tests/test_security_deploy.py` | scans the staging directory and the final tarball; pattern based |
