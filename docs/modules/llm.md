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

**Notes / failure behaviour**
- Reasoning models need large `max_tokens` (>= 4000, the default). The per-call timeout (100 s) is a socket timeout plus the body deadline; a call blocked inside the socket is abandoned by the CLI's signal handler, not cancelled.
- The LLM reply is untrusted data: it enters only through `Space.validate`. This module does not judge content.
- The env file must be mode 600; 640 is rejected, so a service user cannot read it through a group.
