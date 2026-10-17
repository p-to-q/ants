# Evaluation kernels: what real implementations do, and what applies here (R15, 2026-10-17)

An implementation-level survey of cheap lagged-window ridge fits, with the reader's own measurements (Apple M4 Max, numpy 2.5.4 with Accelerate, one thread; the production machine is a different CPU and BLAS, so BLAS-bound ratios may not transfer, gather ratios should). Sources read as code: scikit-learn `_ridge.py`, himalaya, the SearchCast repository (`optuna_ridge.py`), the FITS, SparseTSF and LTSF-Linear data loaders. Not covered: statsmodels, the `scipy.linalg.lstsq` drivers, the fast-covariance literature, engineering blogs; the SearchCast paper was read separately (roadmap, "Fit, tune, finish").

## Where the time goes (reader's profile, whole catalog evaluation, 4 datasets, 3 folds)

Building windows is 50% of an evaluation (1.54 s of 3.07 s for the period core at lookback 336; 3.27 s of 9.17 s for the three-member linear core at lookback 720). The Gram accumulation is the next block (33% of the linear-720 evaluation after the window fix).

## Techniques, and what applies

| technique | source | applies? | measured |
|---|---|---|---|
| gather windows through a strided view instead of an int64 index matrix | SearchCast `get_context_and_horizons` (`unfold` + `index_select`); the repo's own `tinyts.windows` already does it; the catalog path still uses `data.sample_windows` | **yes, first** | gather 4.2 to 7.9 times faster; whole evaluation 1.2 to 2.0 times; metrics identical as float hex strings on 6 specifications; 0 mismatches against the old function on 4 datasets x 4 configurations, with and without the time column |
| Gram in float64 chunks, one solve | SearchCast `RidgeSolver.solve`; `tinyts.Gram` | already done | nothing to gain |
| `syrk` through `matmul(F.T, F)` on one buffer | numpy | already used | 41 ms against 92 ms (n = 25 000, L = 720) |
| Cholesky with `assume_a='pos'` | scikit-learn `_solve_cholesky` | no (scipy is not allowed in the gyms) | `np.linalg.solve` 6.9 ms against scipy `pos` 9.0 ms at L = 720 |
| dual form when windows < lookback | scikit-learn `_solve_cholesky_kernel`, himalaya | no: windows are at least 2 777, lookback at most 720 | |
| one decomposition for a whole alpha path | scikit-learn `_RidgeGCV`, himalaya `_decompose_ridge`, SearchCast (21 Choleskys on one Gram) | not for a trial that uses one strength (ants' `ridge` is a lever); breaks even at about 21 strengths (about 12 at d = 336) | eigh 31 ms at d = 720, 5.2 ms per extra strength against a 6.7 ms solve |
| Toeplitz / Hankel / FFT Gram | own derivation, checked | **no**: exact (relative error 1.5e-14) and 6 to 20 times cheaper for all stride-1 windows of one channel, but ants fits a random capped sample of windows that differs per horizon and member, and `revin` breaks the structure | 0.4 / 1.5 / 7.8 ms against 2.6 / 20.2 / 116 ms at M = 192 / 528 / 1440 |
| reduced-rank regression, closed form | `BasisCore.finish` | already implemented | the `eigh` is 0.456 s of 4.37 s in the basis-720 profile; numpy has no partial spectrum |
| float32 Gram products with float64 accumulation across chunks | `Gram.add` variant | possible, **not bit-identical** | syrk 3.5 times faster (11.6 against 41.3 ms); relative change of `rel_mse` at most 4e-7 (revin) and 1.6e-6 (none, ridge 0.001); four datasets only; needs an explicit tolerance rule and a guard for tiny ridge |
| window caches across horizons and members | | **no**: the sampled windows differ per horizon and member (`rng.choice(total, cap)` with `total` depending on lookback and horizon), so sets are not prefixes of each other | |

## The five changes, ranked, with how each is verified

1. **Per-channel strided gather in `data.sample_windows`**: bit-identical, 1.2 to 2.0 times faster evaluations (period 336: 3.02 s to 1.52 s; linear 720 x 3 members: 8.37 to 5.62; fourier 336: 4.32 to 3.02; basis 720: 4.04 to 2.79). Keep the returned arrays as views of one buffer (returning contiguous copies was slower). Verify with `np.array_equal` and dtype on a grid, then a golden replay on hex metrics.
2. **float32 Gram products**: tolerance 1e-5 on `rel_mse`, a guard for small `ridge * trace / L`, a collinear-lag stress series; new epoch or an explicit tolerance rule.
3. **A larger `CHUNK_ELEMS` (about 4 times)**: 2777 x 720 Gram 20.2 to 15.5 ms; differences at most 1.4e-14 relative; peak memory not measured (check against the unit's memory limit).
4. **Fuse the cast and the centring in `_prep` for `none` and `last`**: intended bit-identical; gain not measured; not for `mean` or `revin` without proof (summation order).
5. **Truncated DFT in `FourierCore`** instead of a full `rfft` (1.44 s of 5.65 s in the fourier-336 profile; only K = 16 to 24 bins are used): rounding-level differences; not benchmarked.

Not worth doing: scipy `pos` solves, an alpha path, Woodbury, Toeplitz.

## What it means for the plan

Change 1 is the same finding as the roadmap's step 48 (measured independently, with a different method: whole-evaluation timing on six specifications and a direct equality check of the old and new function); it is bit-identical and so qualifies for the epoch-equivalence rule once that rule exists. Changes 2 to 5 move last digits and belong in the epoch-3 package with a stated tolerance, or are not worth their risk. The survey's own caveats: the BLAS-bound numbers are from Accelerate on an M4 Max; the production server is x86; changes 3 to 5 are not fully measured; golden replay compared metrics, not stored weights.
