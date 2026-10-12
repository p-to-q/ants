# judge

Judges and the authority ladder (System-One models such as Jev, or LLMs used as classifiers). Library code: nothing in `ants run` or the CLI calls it.

**Interface**
- `JevClient.ask(state, questions)` - documented request format; tolerant response parser that keeps the raw JSON (the early-access response schema was NOT verified).
- `ProposalTriage.score` - asks 'will this candidate land in the top 20%?', logs every prediction with the authority it had at the time.
- `calibration` (AUC, Brier, ECE), `authority` (shadow -> advisory -> delegated, `Policy` thresholds), `resolve_triage` (attach realised outcomes from the ledger).

**Invariants**
- API responses are untrusted: https only, no redirects, size-capped (`llm.http_post_json`); probabilities must be finite numbers in [0, 1] or the answer carries no information; out-of-range stored probabilities are ignored by `calibration`; returned strings are cleaned.
- A judge is `shadow` (logged, ignored) until enough RESOLVED predictions are both informative (AUC) and calibrated (ECE); ranking skill alone is not enough. Defaults: advisory needs n >= 200, AUC >= 0.60, ECE <= 0.10; delegated needs n >= 1000, AUC >= 0.70, ECE <= 0.05.
- Decision kinds listed in `never_delegate` are capped at `advisory`. E0 and E7 are never delegated by convention: `authority` has no knowledge of E0/E7, and `ProposalTriage` does not pass `never_delegate`.
- A judge outage returns the proposals unchanged.

**Notes / failure behaviour**
- `ProposalTriage` only REORDERS proposals (best first) once the judge is `advisory` or `delegated`; it never filters. The "delegated (filters)" authority is not implemented.
- Table `judgements` in the ledger. Needs a Jev key to be exercised for real; tested against a fake server only. `resolve_triage` compares against first-stage results of the history passed in (use one epoch).
