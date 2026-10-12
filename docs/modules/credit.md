# credit

Lever credit report from lineage (batch version; the incremental one lives in `history`).

**Interface**
- `lever_credit`, `lever_weights`, `credit_report`

**Invariants**
- Positive gains only; split equally among levers changed together.

**Notes / failure behaviour:** `proposers` imports `lever_weights`, but the arms take their weights from `History.weights`; `credit_report` is for reports. This batch version scans every `done` trial of the arm (all epochs, not epoch-filtered) and is O(trials) per call.
