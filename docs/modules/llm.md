# llm

OpenAI-compatible multi-endpoint client.

**Interface**
- `LLMClient.chat_json(messages, temperature)` -> `(obj | None, meta)` (never raises), `stats()`.
- `load_endpoints(env_path)` reads `LLM_<TAG>_BASE`, `LLM_<TAG>_KEY`, `LLM_<TAG>_MODEL` (quoted values and `export ` prefixes accepted); refuses a file with any group/other permission bit, checked on the opened file; the model id is never hard-coded.
- `extract_json(text)` (never raises; bounded), `http_post_json(url, body, headers, timeout_s)` and `check_base` (shared with `judge`), `ssl_context()`.

**Invariants**
- Thompson choice between endpoints, fail-over, keys never logged: the key is removed from error text BEFORE truncation and notes are cleaned of control characters (`textsafe.clean_text`). Token counts from the server are coerced; a hostile `usage` cannot make `chat_json` raise, and a failing ledger write is swallowed.
- Transport: https only (http only to loopback), no credentials, query or fragment in the base URL, **redirects are never followed** (urllib would re-send the Authorization header), response body read in chunks with a 4 MB cap and a total deadline, parsed with `textsafe.loads_strict`. TLS verification is never disabled; with no CA bundle the client fails instead.
- Reply parsing: NaN/Infinity/1e999, duplicate keys and nesting deeper than 24 make the reply unusable (None); at most 64 `{` positions are tried in at most 1 MB of text.

**Endpoint health (seeded from ledger)**
- On client creation, each endpoint's ok/bad counts are seeded from the llm_calls of the last 24 hours, so the Thompson bandit does not relearn from scratch after a restart.
- Counts are seeded once at start and only grow within a process (there is no within-process decay or windowing). A recovered endpoint is retried because new successes raise ok and dilute the failure share in Beta(ok+1, bad+1).

**Circuit breaker**
- After N consecutive failures (default 5, `cb_consecutive`) an endpoint is quarantined for T (default 10 min, doubling up to 2 h on repeated trips, reset on success).
- A quarantined endpoint is skipped in the bandit draw unless ALL endpoints are quarantined, in which case `chat_json` returns `(None, {'paused': True, 'until': <wall time>})` immediately without spending a call.
- When all endpoints are paused, the proposers fall back to search exactly as they do when capped.
- Every trip and recovery writes a ledger event (kind `llm_endpoint_state`, payload: endpoint, state, reason, until).

**Retry with backoff**
- For 429, 5xx, timeouts and connection resets: up to 2 retries (3 total attempts) on the SAME endpoint, with exponential backoff and full jitter (base 1 s, cap 20 s).
- Retry-After header honoured (seconds or HTTP date, capped at 60 s).
- NOT retried: `no-json` (model answered with prose), 400-level errors other than 408/429.
- Retries respect the shutdown flag (the process gets SIGTERM): the backoff sleep wakes every 0.25 s and returns within ~1 s of a stop.

**Call tracking**
- Each HTTP attempt is one `llm_calls` row (costs money, counts toward the daily cap).
- A `call_id` (monotonic per logical call) and `attempt` number group retries.
- An `arm` column records which arm made the call.

**Budgets**
- **Daily cap**: per rolling 24 h, per ledger. `ANTS_LLM_MAX_CALLS_PER_DAY` or default 1500, 0 = no cap.
- **Lifetime budget** (`--llm-budget N` / `ANTS_LLM_BUDGET`): total HTTP attempts the arm may spend over its whole life. Counted from the ledger so it survives restarts. When reached the client behaves as capped. An `llm_budget_reached` event is written once.
- Both apply independently: a call is refused when either cap is reached.

**`Ledger.llm_summary(since)`**
- Returns per-endpoint and per-arm: logical calls, attempts, ok logical calls, failure counts by cause class (http_429, http_5xx, timeout, connection, no_json, other).

**Notes / failure behaviour**
- Reasoning models need large `max_tokens` (>= 4000, the default). The per-call timeout (100 s) is a socket timeout plus the body deadline; a call blocked inside the socket is abandoned by the CLI's signal handler, not cancelled.
- The LLM reply is untrusted data: it enters only through `Space.validate`. This module does not judge content.
- The env file must be mode 600; 640 is rejected, so a service user cannot read it through a group.
