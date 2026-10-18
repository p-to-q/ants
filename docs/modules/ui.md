# ui

The last terminal page is **Past** (`9`): cached, per-round history with
read-only details and recorded backup locations. Its information hierarchy and
failure behaviour are documented in [archive_index](archive_index.md).
History and Data load independently of a live ledger or evaluation contract;
contract resolution is deferred until a live Source is opened. A missing current
experiment must not prevent access to preserved evidence or the data catalog.

Human- and agent-facing views: plain dicts that the CLI prints as text or as `--json`. Read-only except `hint` (an event) and the `PAUSE` flag file.

**Interface**
- `snapshot(led, c, arm, workdir, hist=None)` (stages by state, finish rates, disk, `chain_tail_ok` from `verify_tail`, best per cap, operator rates, research paths, LLM calls in the last hour, latest hints), `leaderboard(hist, c, top)`, `show(led, c, ref)` (trial id or spec-key prefix; spec, stage metrics, parent lineage), `log(led, n, kind)`, `hint(led, text)`, `doctor(led, c, arm, workdir)`, `render_top`, `render_show`, `PAUSE_FILE = "PAUSE"`.
- `doctor` returns `(OK|WARN|FAIL, message)` rows. Checks: hash chain (full `verify_chain` below 300 000 events, else `verify_tail(5000)`); disk level (WARN at `warn`, FAIL at `clean` or below); staleness (no finish for 30 min while work is queued = FAIL; paused, or nothing finished yet = WARN); failure rate over the last 200 trials (WARN from 10%, FAIL from 40%); runners past their lease; artifacts not yet archived (WARN from 5000); LLM parse rate over 24 h (WARN below 60%); number of epochs recorded (WARN above 1). `ants doctor` exits 2 on any FAIL.

**Invariants**
- Everything shown that came from outside (hints, `source`, `edit`, `error`, operator names, metrics of a gym, event payload strings) is passed through `textsafe.clean_text`; a hint becomes one plain line of at most 500 characters. Non-numeric metrics from a gym cannot crash `render_show`.
- `hint` text is data for proposer prompts; it cannot change a gate, budget or contract.
- `History` is created without an epoch filter, so `best`, `top` and `show` mix epochs; `doctor` only warns that more than one epoch exists.

**Notes / failure behaviour**
- Not checked by `doctor`: external anchors, the instance lock, state-vs-event audit of `trials`, judge calibration, registry integrity (`Registry.verify`). The 15-minute `ants-health.timer` only runs `ants doctor`.
