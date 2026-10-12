# gyms-toy

Stdlib toy gym with fault injection for CI. Command: `python -m antslab.gyms.toy --stage S --spec F --out D --seed N --attempt A`. Metrics: `val_mse`, `n_params`.

**Interface**
- `TOY_FAULT=kill|hang|nan|exit`, `TOY_FAULT_MOD=N` (the fault fires when `seed % N == 0`), `TOY_SLEEP` (seconds slept after writing the result).

**Invariants**
- Faults fire deterministically by seed; `kill` (SIGKILL of itself) only on attempt 1, so the retry path can be tested; `hang` sleeps an hour; `exit` exits with status 3; `nan` writes a NaN `val_mse`, which `runner.validate_metrics` rejects.
- Noise depends on the stage (s0 0.30, s1 0.08, s2 and final 0.02) and on the seed only.

**Notes / failure behaviour:** Used by the chaos tests (`tests/test_chaos_*.py`, `-m slow` kill -9 test) and the campaign tests. The metrics are not research evidence.
