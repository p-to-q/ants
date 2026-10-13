# Open questions

Research threads, not release scope. Each has a test that would settle it; none is settled. Newest information first.

## 1. How far left can the frontier go?  *(first measurement taken)*
At stage S2 on the four development datasets: unconstrained closed-form ridge (518k parameters) reduces MSE against seasonal naive by
21.0%; a hand-built FITS-like model (7.3k parameters) by 14.9%; the best of a 60-trial ants demo by 15.4% at 1 190 parameters and 12.9%
at 420. That puts the demo level with models six to sixty times larger, on development data, with untuned baselines and selection on the
same windows. **Settles it:** a frontier at 100 / 300 / 1k / 3k parameters from a campaign with ≥ 6 seeds, against (a) the same space
searched by random search at 2× budget, (b) TPE, (c) tuned hand-built SparseTSF / FITS / DLinear re-run in-house, judged on confirmation
data and one sealed look. Risk: a tuned ridge with cheap structure may saturate the space, in which case the honest result is a negative one.

## 2. Does a language model add anything?
Weak, cheap proposers fail mostly on format and drift, not on ideas. In a closed grammar with a filter pipeline the gain over
deterministic mutation may be zero. **Settles it:** operator arms at equal evaluation budget, with format-failure rate, drift per
accepted step and breakthrough rate logged. A null result is reportable.

## 3. Is "next best need not descend from current best" true here?
Arms: greedy, score-weighted children, root restarts, aged eviction. Ancestry of every final best is in the ledger.
**Settles it:** area under the best-so-far curve and final score over many seeds; a stagnation trigger that widens search is one arm.

## 4. Do velocity and acceleration predict anything?
Learning-curve and path statistics are used to allocate budget. They are unvalidated on our own curves. **Settles it:** rank
correlation of early path statistics with final outcome, among the top-k only (proxies fail exactly where decisions are made).

## 5. Where do small models genuinely win?
Candidates: short history, distribution shift, zero-shot transfer of one frozen ~1k-parameter model pre-trained only on the
synthetic suite. A second arena with parameters shared across datasets is the most visible claim available. Not started.

## 7. Does reading the ledger beat a summary of it?  *(from the Meta-Harness study)*
The `llm` operator is shown the eight best specs and a few verified lessons; the `agent` operator reads failures, diffs and queries before it proposes. The paper's own ablation (scores only 34.6 median, plus an LLM summary 34.9, full traces 50.0) is on one task and has not been replicated; follow-ups qualify how far the gain transfers ([research note](research/meta-harness.md)). **Settles it:** `portfolio_agent` against `portfolio_llm` and `portfolio` at equal evaluations and equal LLM calls, ≥ 3 seeds; an arm given the same tokens of random ledger rows (is it reading failures or just more context?); dev-to-confirm gap at equal dev score. A null result is reportable.

## 6. Can a hack-friendly proposer break the evaluator?
A red-team proposer that tries to exploit the gate, the roles and the ledger, with the result published whether or not it succeeds.

## 7. What still depends on human discipline?
Choosing what to claim; the one sealed look; reading the failure log. Listed in the red-team report as the mechanism matures.

## 8. Is the cheap stage a filter or a lottery?  *(measured)*
48 random feasible specs, every one evaluated at S0, S1 and S2 (`scripts/proxy_fidelity.py`, seed 1, [assets/proxy_fidelity.json](../assets/proxy_fidelity.json)).

| pair | Spearman | top-quartile overlap |
|---|---:|---:|
| S0 to S1 | 0.68 | 50% |
| S0 to S2 | 0.67 | 50% |
| S1 to S2 | 0.997 | 100% |

Reading: keeping the best 25% by S0 loses half of the true top quartile, and this sample is wide (random specs), so among near-equal candidates the
correlation will be lower (the 60-trial demo showed 0.20 among promoted specs). S1 and S2 agree almost perfectly, so S2 adds little information for its cost:
its extra dataset (ETTm1) shares a family with ETTh1. **Open:** raise `promote_quantile` at S0, add an independent dev family to S2, or make S2 differ in kind
(more folds, longer history). One seed, one sample; repeat before changing the protocol, which starts a new epoch.
