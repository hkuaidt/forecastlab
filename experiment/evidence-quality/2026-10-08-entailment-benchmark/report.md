# 证据评估 entailment benchmark — 2026-10-08

## Goal

Test whether a third semantic entailment layer can raise 证据评估's post-Boundary-v2 strict-support precision toward 95%/98% without destroying supported recall.

This is a new research branch. Production 证据评估 behavior is unchanged.

## Data

Five prior human / simulated-independent-human audits were merged and exact duplicate `(claim, exact quote)` pairs removed.

Natural gold:
- 135 unique rows;
- 104 entailed;
- 29 partially entailed;
- 1 not entailed;
- 1 unclear.

Two benchmarks are frozen:

### Broad benchmark

`eval/benchmarks/evidence-entailment-hard-v1.json`

317 rows:
- 104 entailed;
- 107 partially entailed;
- 105 not entailed;
- 1 unclear.

It includes deterministic-boundary-style negatives such as numeric shifts and quote-external source attribution. It evaluates the overall semantic validator stack, but is not the primary incremental third-layer benchmark.

### Post-boundary benchmark

`eval/benchmarks/evidence-entailment-post-boundary-v1.json`

239 rows:
- 104 entailed;
- 91 partially entailed;
- 43 not entailed;
- 1 unclear.

Synthetic semantic challenges deliberately avoid simple numeric/source-metadata leakage:
- plan → actual;
- target → actual;
- uncertainty → certainty;
- causal strengthening;
- scope expansion;
- exclusivity strengthening;
- stance/quantifier reversal.

All rows are split by `case_id`, never by row:
- dev: 157;
- held-out test: 82.

The test split is not used for threshold/cascade selection.

## Target

Initial soft-gate target:
- accepted strict precision >= 95%;
- supported recall >= 90%.

A 98% claim requires a larger expert-labelled held-out set and a confidence interval compatible with 98%, not only a point estimate.

## Baseline 1 — same-model DeepSeek judge

Prior bootstrap on the 30-row boundary-v2 audit:
- accepted all 30;
- strict precision stayed 90%;
- non-strict specificity = 0%.

Conclusion: same-model self-judging provides no incremental reliability.

## Baseline 2 — multilingual NLI

Model: `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`, run on Tang GPU in a temporary research environment.

### Post-boundary dev

At entailment threshold 0.95:
- accepted: 69/157;
- accepted strict precision: **68.1%**;
- supported recall: **67.1%**;
- false accepts: 22.

### Held-out post-boundary test

At entailment threshold 0.95:
- accepted: 32/82;
- accepted strict precision: **75.0%**;
- supported recall: **70.6%**;
- false accepts: 8;
- Wilson 95% CI for precision: approximately 57.9%–86.7%.

Conclusion: multilingual NLI is not suitable as a standalone hard gate. It remains an independent cascade signal.

## Baseline 3 — independent local Qwen2.5-1.5B Judge

Model: `Qwen/Qwen2.5-1.5B-Instruct`, run locally on Tang GPU. It is independent from the DeepSeek 证据评估 generator.

### Post-boundary dev

Threshold 0.90:
- accepted: 123/157;
- accepted strict precision: **48.8%**;
- supported recall: **85.7%**;
- false accepts: 63.

Threshold 0.95:
- accepted: 104/157;
- accepted strict precision: **49.0%**;
- supported recall: **72.9%**.

### Held-out post-boundary test

Threshold 0.90:
- accepted: 58/82;
- accepted strict precision: **44.8%**;
- supported recall: **76.5%**;
- false accepts: 32.

Threshold 0.95:
- accepted: 47/82;
- accepted strict precision: **44.7%**;
- supported recall: **61.8%**.

Conclusion: the 1.5B local Judge is too weak for fine-grained quote entailment.

## NLI + Qwen1.5B cascade

Thresholds were selected on dev only.

### Agreement policy

Selected:
- NLI accept: 0.95;
- Qwen accept: 0.95.

Dev:
- precision: 72.5%;
- recall: 52.9%.

Held-out test:
- accepted: 22/82;
- precision: **77.3%**;
- recall: **50.0%**;
- false accepts: 5.

### NLI → LLM fallback

Selected:
- NLI accept: 0.95;
- NLI reject: 0.50;
- Qwen accept: 0.95.

Held-out test:
- precision: **48.2%**;
- recall: **79.4%**.

Neither cascade reaches the 95% / 90% target.

## Current interpretation

The failure of NLI, Qwen1.5B, and their cascade is a useful result: the remaining post-boundary errors are genuinely fine-grained semantic entailment problems and cannot be solved reliably by simply adding a small generic judge.

A stronger independent local Judge (`Qwen/Qwen2.5-3B-Instruct`) was then prepared on Tang using the same frozen prompt/benchmark. The official Hugging Face weight download reached approximately 3.2 GB but stopped making progress because of the current network path; no 3B inference result is therefore reported. This is an environment/download limitation, not a model-quality result, and the partial cache is retained for resumable follow-up.

The completed experiment already answers the immediate engineering question: neither same-model judging, multilingual NLI, Qwen2.5-1.5B, nor their tested cascades justify a production semantic hard gate. The correct next experiment is a stronger independent Judge (resume Qwen3B/7B or configure a genuinely independent API model) on the **same frozen post-boundary dev/test protocol**, without changing prompts based on held-out test errors.

No production hard gate is enabled because the held-out target was not met.
