# textsafe

Hygiene for text that did not originate in our own code (LLM replies, HTTP bodies, hints, gym stderr, free text in the ledger).

**Interface**
- `clean_text(s, limit)` - control characters, C1 controls and bidirectional/invisible characters become visible escapes (`\\x1b`); bounded length. Used by `ui`, `board`, `llm`, `judge`, `contract` before anything is printed, stored as a note, or put in an error message.
- `loads_strict(text)` / `DECODER` / `depth_ok` - JSON that refuses NaN, Infinity, `1e999`, duplicate keys and nesting deeper than 24; never raises `RecursionError`.

**Invariants**
- Pure functions, stdlib only, no I/O.
- Cleaning is for DISPLAY and AUDIT notes; data that is validated elsewhere (`Space.validate`) is never rewritten.

**Notes / failure behaviour**
- Importers today: `ui`, `board`, `llm`, `judge`, `contract`, `agent`, `workspace`, `tui/model`, `tui/views`. Not applied: the `failure x N` lines of `dream`, `report` facts, `maintainer` messages, and the ledger event payloads themselves (stored as given; only the display paths clean them).
- `clean_text` shortens (with an ellipsis) rather than failing; `loads_strict` raises `ValueError` (`HostileJSON` for policy violations).
