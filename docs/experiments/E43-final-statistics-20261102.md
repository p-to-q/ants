# E43 registered final statistical output

32 runs; arms ['portfolio', 'portfolio_screen']; seeds [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]; budget 50400 CPU-s; control `portfolio`, treatment `portfolio_screen`.

## Validity checks

| run | epoch ok | definition hashes | starts | chain | CPU-s | deepest-stage trials | reached budget |
|---|---|---|---|---|---|---|---|
| portfolio_s1 | True | 1 | 1 | True | 50448 | 397 | True |
| portfolio_s2 | True | 1 | 1 | True | 50425 | 304 | True |
| portfolio_s3 | True | 1 | 1 | True | 50411 | 467 | True |
| portfolio_s4 | True | 1 | 1 | True | 50414 | 556 | True |
| portfolio_s5 | True | 1 | 1 | True | 50403 | 539 | True |
| portfolio_s6 | True | 1 | 1 | True | 50401 | 424 | True |
| portfolio_s7 | True | 1 | 1 | True | 50403 | 388 | True |
| portfolio_s8 | True | 1 | 1 | True | 50412 | 376 | True |
| portfolio_s9 | True | 1 | 1 | True | 50427 | 296 | True |
| portfolio_s10 | True | 1 | 1 | True | 50409 | 592 | True |
| portfolio_s11 | True | 1 | 1 | True | 50431 | 562 | True |
| portfolio_s12 | True | 1 | 1 | True | 50421 | 316 | True |
| portfolio_s13 | True | 1 | 1 | True | 50452 | 401 | True |
| portfolio_s14 | True | 1 | 1 | True | 50444 | 430 | True |
| portfolio_s15 | True | 1 | 1 | True | 50402 | 502 | True |
| portfolio_s16 | True | 1 | 1 | True | 50489 | 282 | True |
| portfolio_screen_s1 | True | 1 | 1 | True | 50437 | 408 | True |
| portfolio_screen_s2 | True | 1 | 1 | True | 50456 | 327 | True |
| portfolio_screen_s3 | True | 1 | 1 | True | 50467 | 418 | True |
| portfolio_screen_s4 | True | 1 | 1 | True | 50436 | 547 | True |
| portfolio_screen_s5 | True | 1 | 1 | True | 50418 | 527 | True |
| portfolio_screen_s6 | True | 1 | 1 | True | 50405 | 487 | True |
| portfolio_screen_s7 | True | 1 | 1 | True | 50408 | 453 | True |
| portfolio_screen_s8 | True | 1 | 1 | True | 50440 | 500 | True |
| portfolio_screen_s9 | True | 1 | 1 | True | 50492 | 328 | True |
| portfolio_screen_s10 | True | 1 | 1 | True | 50403 | 578 | True |
| portfolio_screen_s11 | True | 1 | 1 | True | 50419 | 536 | True |
| portfolio_screen_s12 | True | 1 | 1 | True | 50424 | 297 | True |
| portfolio_screen_s13 | True | 1 | 1 | True | 50419 | 422 | True |
| portfolio_screen_s14 | True | 1 | 1 | True | 50506 | 415 | True |
| portfolio_screen_s15 | True | 1 | 1 | True | 50421 | 547 | True |
| portfolio_screen_s16 | True | 1 | 1 | True | 50409 | 315 | True |

## Endpoints at the budget (median over seeds; best deepest-stage rel_mse among models of at most the cap)

| arm | <= 100 | <= 300 | <= 1000 | <= 3000 | s1 share of CPU | P(rel_mse <= 0.80 at 3000) |
|---|---|---|---|---|---|---|
| portfolio | 0.9015 (0.8247 to 0.9738, n=16) | 0.8153 (0.8069 to 0.8807, n=16) | 0.7875 (0.7829 to 0.8270, n=16) | 0.7830 (0.7782 to 0.7993, n=16) | 0.55 | 1.00 |
| portfolio_screen | 0.9081 (0.8285 to 0.9712, n=16) | 0.8156 (0.8069 to 0.8610, n=16) | 0.7925 (0.7799 to 0.8271, n=16) | 0.7805 (0.7763 to 0.7835, n=16) | 0.50 | 1.00 |

## Primary: cap 300, portfolio_screen minus portfolio (negative is better)

Values are censored at the reference (1.0) for a run with no deepest-stage result under the cap; the complete-case result is the first sensitivity row. The per-look level is 0.0294 (two looks, Pocock; see the protocol).

| endpoint | pairs | median diff | Hodges-Lehmann (exact interval) | exact Wilcoxon p | exact permutation p (mean) | A12 (95% interval) |
|---|---|---|---|---|---|---|
| PRIMARY: value at budget, censored | 16 | +0.0000 | -0.0002 (-0.0065 to +0.0008, coverage 0.956) | 0.5771 | 0.6465 | 0.50 (0.29 to 0.71) |
| sensitivity: complete cases only | 16 | +0.0000 | -0.0002 (-0.0065 to +0.0008, coverage 0.956) | 0.5771 | 0.6465 | 0.50 (0.29 to 0.70) |
| sensitivity: value at 14400 s | 16 | +0.0000 | +0.0000 (-0.0018 to +0.0000, coverage 0.956) | 0.5000 | 0.5000 | 0.51 (0.30 to 0.71) |
| sensitivity: value at 28800 s | 16 | +0.0000 | +0.0000 (-0.0041 to +0.0020, coverage 0.956) | 0.9453 | 0.9531 | 0.54 (0.34 to 0.73) |

Mann-Whitney p (unpaired, sensitivity) 1.0000; sd of the paired differences 0.0124; approximate minimum detectable effect at 80% power 0.010.

**Reading: futility at this look: the median paired difference is not in favour of the screen.**

## Secondary (exploratory; intervals, not confirmations; the family has 20 rows)

| endpoint | pairs | median diff | Hodges-Lehmann (exact interval) | exact Wilcoxon p | exact permutation p (mean) | A12 (95% interval) |
|---|---|---|---|---|---|---|
| value at 28800 s, cap 100 | 16 | +0.0022 | +0.0140 (+0.0000 to +0.0301, coverage 0.956) | 0.0640 | 0.0474 | 0.39 (0.20 to 0.59) |
| value at 50400 s, cap 100 | 16 | +0.0050 | +0.0153 (+0.0003 to +0.0334, coverage 0.956) | 0.0215 | 0.0310 | 0.37 (0.19 to 0.57) |
| area under best-so-far, cap 100 | 16 | +0.0017 | +0.0078 (+0.0000 to +0.0192, coverage 0.956) | 0.0803 | 0.0881 | 0.42 (0.23 to 0.62) |
| CPU-s to within 3% of the pooled best (post hoc target; censored at the budget), cap 100 | 16 | +0.0000 | +0.0000 (+0.0000 to +0.0000, coverage 0.956) | 1.0000 | 1.0000 | 0.47 (0.38 to 0.57) |
| value at 28800 s, cap 300 | 16 | +0.0000 | +0.0000 (-0.0041 to +0.0020, coverage 0.956) | 0.9453 | 0.9531 | 0.54 (0.32 to 0.72) |
| value at 50400 s, cap 300 | 16 | +0.0000 | -0.0002 (-0.0065 to +0.0008, coverage 0.956) | 0.5771 | 0.6465 | 0.50 (0.29 to 0.70) |
| area under best-so-far, cap 300 | 16 | +0.0000 | +0.0000 (-0.0013 to +0.0034, coverage 0.956) | 0.9097 | 0.9966 | 0.51 (0.31 to 0.71) |
| CPU-s to within 3% of the pooled best (post hoc target; censored at the budget), cap 300 | 16 | +6.5459 | +18.3039 (-4.8860 to +3805.0252, coverage 0.956) | 0.1909 | 0.7622 | 0.48 (0.29 to 0.69) |
| value at 28800 s, cap 1000 | 16 | +0.0000 | +0.0000 (-0.0022 to +0.0027, coverage 0.956) | 1.0000 | 0.9265 | 0.51 (0.30 to 0.71) |
| value at 50400 s, cap 1000 | 16 | +0.0000 | +0.0022 (-0.0013 to +0.0059, coverage 0.956) | 0.2524 | 0.1120 | 0.44 (0.23 to 0.65) |
| area under best-so-far, cap 1000 | 16 | -0.0001 | +0.0007 (-0.0013 to +0.0028, coverage 0.956) | 0.5614 | 0.3998 | 0.51 (0.30 to 0.71) |
| CPU-s to within 3% of the pooled best (post hoc target; censored at the budget), cap 1000 | 16 | +1.8194 | +4.3909 (-48.2226 to +8812.0446, coverage 0.956) | 0.7148 | 0.6007 | 0.49 (0.29 to 0.70) |
| value at 28800 s, cap 3000 | 16 | -0.0023 | -0.0025 (-0.0045 to -0.0007, coverage 0.956) | 0.0052 | 0.0040 | 0.71 (0.51 to 0.88) |
| value at 50400 s, cap 3000 | 16 | -0.0024 | -0.0026 (-0.0043 to -0.0013, coverage 0.956) | 0.0003 | 0.0002 | 0.86 (0.72 to 0.97) |
| area under best-so-far, cap 3000 | 16 | -0.0013 | -0.0015 (-0.0029 to +0.0000, coverage 0.956) | 0.0507 | 0.0438 | 0.59 (0.38 to 0.79) |
| CPU-s to within 3% of the pooled best (post hoc target; censored at the budget), cap 3000 | 16 | -2.3057 | -4.7349 (-37.1061 to +14.6196, coverage 0.956) | 0.6685 | 0.9253 | 0.50 (0.30 to 0.71) |

## Restarts against one long run (roadmap step 44)

Mean over all subsets of K seeds of the best value at budget/K, against the mean value of one seed at the full budget.

| arm | cap | one run, full budget | K=2 | K=4 | K=8 |
|---|---|---|---|---|---|
| portfolio | 100 | 0.8921 | 0.8804 | 0.8804 | 0.8868 |
| portfolio | 300 | 0.8250 | 0.8181 | 0.8184 | 0.8130 |
| portfolio | 1000 | 0.7920 | 0.7890 | 0.7869 | 0.7905 |
| portfolio | 3000 | 0.7838 | 0.7836 | 0.7833 | 0.7877 |
| portfolio_screen | 100 | 0.9096 | 0.8987 | 0.8785 | 0.8868 |
| portfolio_screen | 300 | 0.8236 | 0.8172 | 0.8183 | 0.8130 |
| portfolio_screen | 1000 | 0.7949 | 0.7886 | 0.7871 | 0.7905 |
| portfolio_screen | 3000 | 0.7800 | 0.7807 | 0.7829 | 0.7877 |
