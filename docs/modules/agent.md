# Agent operator (`src/antslab/agent.py`)

An LLM operator that reads the ledger through a closed set of read-only tools before it proposes candidate specs. It returns specs only: no code, no scores, no access to the gate or registry.

## Tool generations

| generation | tools | MAX_TOOL_CALLS | arm keys |
|---|---|---|---|
| v1 (default) | `sql`, `show`, `diff`, `best`, `failures` | 6 | `agent`, `portfolio_agent` |
| v2 | v1 + `check`, `neighbors`, `effects`, `predict`, `scorecard` | 8 | `agent2`, `portfolio_agent2` |

Select with `AgentProposer(..., tools="v2")`. The arm definition includes a `tools_gen` key only for v2, so v1 definition hashes are unchanged.
CLI client setup, definition checks and the seed runner recognize both v2 arms
as model-calling arms; the existing explicit multi-seed model-call guard applies.

## v1 tools

| tool | arguments | answer |
|---|---|---|
| `sql` | `{"query": "SELECT ..."}` | columns + rows from the read-only `trials` table (denied: events, unseals, llm_calls, artifacts, epochs; denied column: out_dir) |
| `show` | `{"ref": <id or key prefix>}` | spec, per-stage metrics, error, lineage of one trial (own arm only) |
| `diff` | `{"a": <id>, "b": <id>}` | levers that differ between two trials and both metrics |
| `best` | `{"top": 5}` | best specs per cap, ranked by deepest stage |
| `failures` | `{}` | most common error messages among failed trials |

## v2 tools (in addition to v1)

| tool | arguments | answer |
|---|---|---|
| `check` | `{"spec": {...}}` | validate a candidate exactly as the campaign would: space validation, static_cost against caps, memory against mem_cap_mb, cap bin, duplicate/near-duplicate detection (with metrics), 3 nearest evaluated specs by Encoder distance |
| `neighbors` | `{"ref": <id or key prefix>, "k": 5}` | k nearest evaluated specs by Encoder distance, with metrics and differing levers |
| `effects` | `{"lever": "optional", "cap": optional}` | mutation-effect table from lineage: per lever and (from_bin, to_bin), n pairs, mean/median delta at s0 and deepest stage, improved count. Observational only. Top 25 rows. |
| `predict` | `{"spec": {...}}` | surrogate (ExtraTrees) predicted deepest-stage value and spread; returns "no model yet" with row count when < 150 rows; numpy imported lazily |
| `scorecard` | `{}` | the agent's own track record: for recorded expectations, share correct by (expect direction), counted by deterministic comparison of child vs parent value |

## Protocol v2

Each proposal may carry:
- `parent`: trial id or spec key it was derived from (validated against the arm's ledger; wrong parent dropped, spec still considered)
- `expect`: one of `"better"`, `"worse"`, `"same"` for the primary metric versus the parent
- `why`: up to 160 characters, cleaned with `textsafe.clean_text`

The proposer sets `origin_key` from `parent` and `edit` from the levers that actually differ. One hash-chained `agent_expectation` event is written per accepted proposal that has a valid parent and expect. The words in `why` are stored but never used by any code that decides anything.

Replies in v1 shape (plain specs without parent/expect/why) are accepted by v2.
Expectations carry arm and epoch. Scorecards ignore unscoped/foreign events and
wait for parent and child to have the same stage; effect summaries likewise do
not compare different-depth values. Even-sample medians average the middle two.
The predictor fits deepest-stage rows only. These are observational diagnostics,
not promotion evidence or calibrated probabilities of a breakthrough.

## Pinned arms

The existing arms `portfolio_agent` and `agent` are pinned: their tool set, prompt and behaviour must not change. Tests pin the v1 TOOLS hash and verify that v1 arm definition hashes are unchanged. New behaviour lives in new arms (`agent2`, `portfolio_agent2`).

## Security invariants

- Tools are closed: no shell, no file access.
- A job reads through its own read-only connection (`_Job`), never the campaign's.
- Every tool call is a hash-chained `agent_tool` event.
- Every reply is validated like any other proposal (space, feasibility, novelty).
- Hostile inputs (huge numbers, nested objects, long strings) produce text error answers, never exceptions.
- The `why` field is stored but never read by scoring, gating, or decision code.
