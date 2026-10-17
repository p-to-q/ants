# The training lifecycle of forecasting models: what the literature says, and what it means for ants

Research item R14 of the [roadmap](../roadmap.md), read 2026-10-17. R1 to R13 covered *search* ([search-landscape](search-landscape.md)); this memo covers *training*. Method: PDFs of the papers below were downloaded and read as text (abstract, setup, tables); a web search located them; one abstract (Li et al. 2020) came through a summarising fetch. Numbers are quoted from the source text. **Nothing was run on our system.** Verdicts are hypotheses for the roadmap, not results.

Frame: ants fits 100 to 3 000 parameter specs in closed form (ridge) and scores `rel_mse` against seasonal naive. The smallest pre-trained model found below has 0.2M parameters, about 67 times our largest cap. **No paper found reports a pre-trained, distilled, pruned or adapted forecaster at or under 3 000 deployed parameters.** The one gradient-trained linear forecaster family that is smaller (SparseTSF 0.92K, MixLinear 45 to 176) was trained from scratch and never compared with a closed-form fit.

## Verdicts

| stage | verdict for ants | one-line reason |
|---|---|---|
| 1 pre-training | **reference only** (step 14: one zero-shot model as ceiling) | smallest pre-trained model found is 0.2M; no result at our caps |
| 2 continued pre-training, domain adaptation | **out of scope** | no general model is a part of ants; there is nothing to continue |
| 3a fine-tuning, adapters | **separate gym** (step 23/38) | adapters of 2 400 parameters exist, but on top of an 8.3M frozen model |
| 3b RL, preference | **out of scope** for the model; **reference only** for the agent arms | RL exists for forecasting (LLM or foundation-model policies); no preference/DPO paper found; none against a closed form |
| 3c distillation | **separate gym** (step 23) | smallest student found is about 1M |
| 3d pruning | **separate gym** (step 23) | evidence is on million-parameter Transformers |
| 3e quantisation, QAT | **lever, after export** (step 26); QAT is a separate gym | the only tiny-forecaster QAT data shows 4-bit is costly |
| 4 gradient against closed form | **lever already in place** (ridge core); the comparison is a **separate gym** (step 23) | closed form wins 72% of Toner and Darlow's settings, at far more than 3k parameters |
| 5 train-small against shrink-large | **separate gym** (step 23 is the experiment) | no forecasting comparison at a fixed small budget exists |

## 1. Pre-training of foundation models

| model | parameters | pre-training data | what the paper reports |
|---|---|---|---|
| [Chronos](https://arxiv.org/abs/2403.07815) | 20M, 46M, 200M, 710M | 28 datasets, about 890K series, 84B observations, plus synthetic Gaussian-process series | zero-shot "comparable and occasionally superior" to task-specific models on 27 datasets; Mini (20M) beats Moirai-Large (311M) on the in-domain set; random initialisation matches or beats language-model initialisation |
| [TimesFM](https://arxiv.org/abs/2310.10688) | 17M, 70M, 200M | O(100B) timepoints | top model on Monash (scaled MAE, normalised by last-value naive), within significance of N-BEATS; error falls with FLOPs across its three sizes |
| [Moirai](https://arxiv.org/abs/2402.02592) | 14M, 91M, 311M | LOTSA, 27B observations | zero-shot long-horizon ETTh1 MSE 0.400 (14M), 0.434 (91M), 0.510 (311M) against DLinear 0.456; the authors call the size-performance relation "tenuous" |
| [Lag-Llama](https://arxiv.org/abs/2310.08278) | 2 449 299 | 7 965 series, about 352M windows | average rank 6.714 zero-shot, 2.786 fine-tuned, over supervised baselines (AutoARIMA, N-BEATS, PatchTST, TFT); no linear-regression baseline |
| [TTM](https://arxiv.org/abs/2401.03955) | from 1M | about 1B samples | zero-shot beats Chronos by 17 to 32% and Lag-Llama by 40% on its own protocol; its "full-shot" rivals are Moirai-paper numbers at context 96, against its own context of 512 and more |
| [Timer](https://arxiv.org/abs/2402.02368) | 1M to 50M | UTSD, up to 1B points | few-shot error falls 14.7% and 20.6% going from 1M to 4M, 25.1% and 18.2% from 3M to 50M; baselines are PatchTST and iTransformer |
| [MOMENT](https://arxiv.org/abs/2402.03885) | 40M, 125M, 385M | 1.23B timestamps | zero-shot M3/M4: "Statistical methods such as Theta and ETS outperformed their deeper counterparts" |
| [Reverso](https://arxiv.org/abs/2602.17634) (preprint) | 0.2M, 0.55M, 2.6M | GIFT-Eval pre-training set | GIFT-Eval MASE 0.706 at 2.6M, ahead of TTM (1M) and Super-Linear (2.5M) |
| [Super-Linear](https://arxiv.org/abs/2509.15105) (TMLR 2026) | 2.5M | multi-frequency, resampled | pre-trained mixture of linear experts; "up to 26.2%" lower average MSE than Chronos and TimesFM |

Against tuned linear or closed-form baselines the evidence is thin. [GIFT-Eval](https://arxiv.org/abs/2410.10393) normalises by seasonal naive, as `rel_mse` does: PatchTST trained per dataset has the best average score, the scaling law "does not consistently" hold, and DLinear is best or second best in 0 of 97 configurations. [Soni et al. 2026](https://arxiv.org/abs/2605.24381) (Google, preprint) report M4 MASE 1.212 for Chronos-T5-Base, 1.234 for TimesFM 2.5, 1.211 for DLinear, but DLinear is far worse on traffic (MASE 1.165 against 0.729 for Chronos), and their energy and exchange sets hold 7 and 8 series. **No paper found compares a pre-trained model with a tuned closed-form ridge on one protocol**; SearchCast (section 4) does not include foundation models.

Reading for ants: pre-training buys breadth at 10M and above. Below 1M the only data points are Reverso (0.2M, 0.55M) and TTM (1M), still 67 to 333 times our largest cap. Use one zero-shot model as a ceiling, as step 14 says; evaluate it on windows outside its published training sets (Chronos and GIFT-Eval separate such sets; ours is the sealed confirm data).

## 2. Continued pre-training and domain adaptation

- [TimesFM-ICF](https://arxiv.org/abs/2410.24087) continues the pre-training of TimesFM with in-context examples; its abstract says it "rivals" a model fine-tuned on the target domain, and its Figure 6 caption says zero-shot ICF beats per-dataset fine-tuned TimesFM on Monash.
- Chronos-T5 Small fine-tuned per dataset (1 000 steps, learning rate 0.001) takes first place on its 27 held-out datasets ([Chronos](https://arxiv.org/abs/2403.07815), section 5.5).
- [R-Tuning](https://arxiv.org/abs/2511.11685): fine-tuning on new data alone "often leads to catastrophic forgetting" (abstract; I did not check its experiments).
- [Gupta et al.](https://arxiv.org/abs/2405.10216): full fine-tuning lowered heart-rate accuracy for Moirai Base and Large and for Chronos, while LoRA kept or improved it (one clinical task).

Verdict: out of scope. These results are about models with something to forget. The transferable lesson is about evaluation, not method: adaptation gains are measured on the target dataset, which in ants would be the development data.

## 3. Post-training

### 3a. Supervised fine-tuning and adapters

| source | measured | note |
|---|---|---|
| [Gupta et al. 2024](https://arxiv.org/abs/2405.10216) | LoRA (rank 2) on Lag-Llama, Moirai, Chronos for ICU vital signs (eICU) | LoRA parameters from 16 thousand to 0.4M; LoRA at or above full fine-tuning on Chronos; one clinical task |
| [Gupta et al. 2024b](https://arxiv.org/abs/2409.11302) | BitFit, LayerNorm tuning, VeRA, FourierFT on Chronos | FourierFT on Chronos-Tiny trains 2 400 parameters and "surpasses" a 700K-parameter model trained from scratch, on the same single task; Chronos-Tiny has 8.3M parameters |
| [Tan et al. 2024](https://arxiv.org/abs/2406.16964) | ablations of three LLM-based forecasters | removing the language model "does not degrade forecasting performance"; pre-trained LLMs "do no better than models trained from scratch" |
| [Zhao et al. 2025](https://arxiv.org/abs/2505.23195) | fine-tuning 7 foundation models on 6 benchmarks | "even after fine-tuning" they do not consistently beat PatchTST trained on full data; pruning before fine-tuning cuts error by up to 22% |
| [TRACE](https://arxiv.org/abs/2503.16991) | gated LoRA selection, smaller forecasting heads | abstract only |

Verdict: separate gym. A 2 400-parameter adapter exists, but the deployed model is the 8.3M backbone. In ants the cap counts the deployed model, so an adapter on a frozen ants spec would count both parts. Only the gym of step 38 can say whether a gradient-trained part inside a cap beats spending the same parameters on the ridge.

### 3b. Reinforcement and preference learning

It exists for forecasting, all of it 2025 to 2026 and mostly preprints, and none against a closed form:

- [TimeRFT](https://arxiv.org/abs/2605.00015): GRPO on a pre-trained foundation model (MOIRAI-MoE-Small main), reward from accuracy, variability and spectral terms; average 10.17% lower MSE than supervised fine-tuning and LoRA.
- [Time-R1](https://arxiv.org/abs/2506.10630) (CIKM 2026): an LLM reasons, then forecasts; reward-trained; result stated as "competitive".
- [PostTime](https://arxiv.org/abs/2605.29401): SFT plus RL with verifiable rewards teaches a 4B LLM to revise TimesFM-2.5 forecasts using text context.
- [Cast-R1](https://arxiv.org/abs/2602.13802): forecasting as a sequential decision process with tools, trained with GRPO. Nearest to our agent arms.

**No preference-optimisation (DPO) paper for forecasting was found** (one search; a library named nanoDPO appeared and was not examined). Verdict: out of scope for models; the policy-learning papers are reference reading for R8 and R13.

### 3c. Distillation

- [DistilTS](https://arxiv.org/abs/2601.12785) (Jan 2026): students are DLinear and iTransformer at hidden size 512; parameters "up to 1/150"; the paper says no earlier work addressed distillation of foundation models. Ablation (teacher TimeMoE-50M): ETTh1 average MSE 0.414 without its two components, 0.395 with them; a 200M teacher gave only limited additional benefit.
- [TimeDistill](https://arxiv.org/abs/2502.15016): MLP student of 1.1M against a 132.1M teacher, up to 18.6% better than the same MLP trained alone, and it "surpass[es] teacher models" on eight datasets.

Verdict: separate gym. Smallest student found is 1.1M. Our roadmap lists a distillation weight as a simulated lever; no source supports distillation at 3k.

### 3d. Pruning

[Kiefer et al.](https://arxiv.org/abs/2412.12883): unstructured magnitude pruning keeps accuracy to about 50% sparsity in nearly all Transformer forecasters, to 90% only for models with Fourier decomposition; fine-tuning after pruning is needed; structured pruning gave no significant time savings. [Zhao et al.](https://arxiv.org/abs/2505.23195) prune foundation models before fine-tuning. [µNAS](https://arxiv.org/abs/2010.14246) (image classification only): "pruning is essential for finding models with low resource usage" inside an aging-evolution search. **No lottery-ticket paper for forecasting was found.**

### 3e. Quantisation and quantisation-aware training

[Ling et al.](https://arxiv.org/abs/2407.11041) trained tiny Transformers with QAT (897 to 50 561 parameters). PeMS test RMSE for 3 329 parameters: FP32 0.1615, 8-bit 0.1685, 6-bit 0.1988, 4-bit 0.3117; for 897 parameters: 0.1609, 0.1809, 0.2227, 0.4224. Their abstract reports 0.63% worse than a related 8-bit model, which is not a comparison with their own FP32. They also find lower bit width "does not consistently" cut latency or energy. **No paper quantising a foundation model for forecasting was found**; the [post-training survey](https://arxiv.org/abs/2607.20002) names quantisation without citing one.

Verdict: step 26 (export, then measure int8 against float32) is the lever. Rounding ridge weights needs no gradients; QAT does, so it belongs to the gradient gym.

## 4. Gradient training against closed form, under 3 000 parameters

| source | parameters | training | finding |
|---|---|---|---|
| [DLinear](https://arxiv.org/abs/2205.13504) | 2TL (TL for NLinear) | SGD | one linear layer beats Transformers "in all cases" on nine datasets |
| [FITS](https://arxiv.org/abs/2307.03756) | about 10k | gradient | comparable to state-of-the-art models |
| [SparseTSF](https://arxiv.org/abs/2405.00946) | 0.92K at lookback 720 | Adam, 30 epochs | ahead of the best baseline on ETTh1 and ETTh2 at all 8 entries (0.004 to 0.019 MSE); behind by 0.001 to 0.009 on Electricity at three of four horizons and by 0.009 to 0.022 on Traffic at all four |
| [MixLinear](https://arxiv.org/abs/2410.02081) (ICLR 2026) | 45 to 176 | gradient | 5.3% better than SparseTSF on ETTh1 at 336; compared only with gradient-trained models |
| [Toner and Darlow](https://arxiv.org/abs/2403.14587) (ICML 2024) | context 720 in the setup read, so 720 x T for W alone | SGD against OLS | closed form better in 23 of 32 settings (72%); FITS wins on small hourly ETT, which the authors attribute to implicit bias constraint |
| [SearchCast](https://arxiv.org/abs/2606.27282) (preprint, Sakana) | H x L, L up to about 2 000 | ridge, TPE, 20 trials per cell, 3-fold CV | best linear average on 7 of 8 sets; 4.8% (ETTm2), 4.3% (ETTh1), 14.8% (Exchange) below OLS; beats nonlinear baselines on 6 of 8, loses on Electricity (tie) and Traffic (0.404 against 0.391) |
| [Alinear](https://arxiv.org/abs/2505.10172) (preprint) | "k-level" | Adam | claims competitiveness at under 1% of large models' parameters; not examined further |

Reading: the evidence for closed form is at 10^4 to 10^5 parameters; the evidence for gradient training is at 10^2 to 10^3, from scratch, with no closed-form comparator. **No paper compares the two at a parameter cap**, which is the comparison step 23 would run. Premise warning already in [benchmark.md](../benchmark.md): SearchCast covers closed-form plus searched preprocessing, uncapped.

Verdict: the ridge core is the incumbent; a gradient profile on the same caps and data is a separate gym whose only job is to answer this question.

## 5. Train-small against shrink-large

- [Li et al. 2020](https://arxiv.org/abs/2002.11794) (language Transformers): "heavily compressed large models" beat lightly compressed small ones (abstract; no forecasting).
- [Kiefer et al.](https://arxiv.org/abs/2412.12883), the one forecasting comparison: on ETTm2 a model with the embedding scaled down tenfold had lower test loss than the large one (Transformer MSE down 38%); on ENTSO-E, larger models had lower error before and after pruning, so "training a larger model, pruning it" was not replaced by a smaller model. Sizes are millions; Transformers only.
- [µNAS](https://arxiv.org/abs/2010.14246): search with pruning inside, image tasks.

Result: mixed, and size-dependent on dataset size. Nothing at a fixed 3k budget, nothing with a linear core.

## What this changes in the plan

1. Step 14 stays as written; add the Reverso/TTM sizes to the table of what a pre-trained model offers.
2. Step 23 is a real gap, not a repeat. Design it so the two routes meet at the same cap: gradient-train a larger model, shrink it to the cap (prune, distil, quantise), and train the capped model directly, against ridge at the cap. Include Kiefer's warning that outcomes flip with dataset size.
3. Step 38 order: gradient gym with a trainable part inside the cap first; distillation and QAT after; RL and preference learning not at all unless a task has a reward the model acts on.
4. Adapter cap accounting must count the frozen part.

## Not verified

- A paper titled "Linear models are all you need?" by Toner and Darlow: not found; the paper read is *An Analysis of Linear Time Series Forecasting Models*.
- Experiments of R-Tuning, TRACE, Alinear, Time-R1, PostTime and Cast-R1 (abstracts and set-up only); Reverso-Nano and Reverso-Small scores (shown only in a figure).
- The optimiser and training details of MixLinear and FITS beyond "gradient".
- Secondary claims taken from the [survey](https://arxiv.org/abs/2607.20002): that process-model experiments show small or vanishing fine-tuning gains.
- Whether any DPO paper for forecasting exists beyond one search; whether any lottery-ticket or quantisation paper for forecasting exists beyond one search each.
- Chronos and TimesFM aggregate scores (in figures only), Time-MoE and Chronos-2 (downloaded, not read).
- DLinear numbers in the TimesFM and Moirai tables are copied from other papers (10% data; lookback 96) and not reproduced.
