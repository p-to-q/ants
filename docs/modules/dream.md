# dream

Offline consolidation of the ledger into verified lessons.

**Interface**
- `dream(ledger, c, arm, client=None, max_lines=12)` -> lessons, also written as a hash-chained `dream` event; `latest_memory(ledger, arm)` (text that the `*_dream` arms paste into LLM prompts); `effects`, `failure_clusters`.
- Run by `ants run` every `--dream-every` finished trials (default 100) for the `llm_dream` and `portfolio_dream` arms only.

**Invariants**
- An LLM may word a lesson but every claim is re-checked against the statistics (`better` needs a positive gain, `worse` a negative one for that exact parameter/value pair); unsupported or structurally wrong claims are dropped and counted (`unsupported_dropped`). The model's `note` is reduced to `[A-Za-z0-9 .,=%()/+-]` and 100 characters.
- Statistics use first-stage `done` trials only, tercile buckets for numeric parameters, at least `min_n` = 3 per bucket.

**Notes / failure behaviour**
- Not epoch-filtered: it reads every `done` trial of the arm, so lessons can mix measurement machinery.
- The `failure x N: <message>` lines are built from the first 60 characters of each failed trial's `error` text (gym stderr) before the first `:`; that text is not passed through `textsafe.clean_text`, and the lines end up in later prompts. Control characters or newlines from a hostile gym error could therefore reach a prompt.
