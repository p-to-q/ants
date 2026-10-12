# report

Tables, facts, number-linted LLM narrative. Library code: no CLI command calls it.

**Interface**
- `facts(ledger, c, arms, baselines)`, `tables_md`, `lint_numbers(text, facts, rel_tol=0.01)`, `draft_narrative(client, facts)`, `write_report(ledger, c, arms, out_dir, client, baselines)` (writes `facts.json`, `report.md`, and a `report_narrative` event).

**Invariants**
- A number in LLM prose passes the lint if it matches ANY number in `facts.json` (keys and strings included) within display rounding or 1% relative; inline code spans, list markers and headings are ignored. The lint is not context-aware: a number that equals an unrelated fact passes.
- A narrative with untraceable numbers is shown behind an `UNVERIFIED` banner listing them, never silently accepted; `draft_narrative` allows one repair round.

**Notes / failure behaviour**
- `facts` uses `rank_specs` and `trials` without an epoch filter, so a report mixes epochs. It includes `sealed_evaluations_used` and the chain status.
- The replay pack promised in [E7](../evaluation/E7-claim.md) is not produced by this module.
