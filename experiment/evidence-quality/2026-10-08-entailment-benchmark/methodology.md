# 证据评估 entailment benchmark v1 — methodology

## Purpose

This benchmark evaluates the semantic layer after 证据评估 exact-quote extraction. It separates two questions:

1. **Broad semantic validation** — can a judge distinguish correct quote entailment from obvious factual/metadata perturbations?
2. **Post-boundary incremental value** — after the deterministic structural + boundary validators have already done their job, can an independent semantic judge catch the subtler remaining errors?

The second question is the primary one for deciding whether ForecastLab should add a third production gate.

## Natural gold

Five historical human / simulated-independent-human audit artifacts are merged:

- boundary-v2 Reviewer 2;
- boundary-v1 Reviewer 2;
- semantic-after audit;
- semantic-before audit;
- robustness audit.

Exact duplicate `(claim, exact_quotes)` pairs are removed.

Result: **135 unique natural rows**:

- 104 `entailed`;
- 29 `partially_entailed`;
- 1 `not_entailed`;
- 1 `unclear`.

The natural rows retain the original case/category and audit note.

## Broad benchmark

File: `eval/benchmarks/evidence-entailment-hard-v1.json`

The broad set adds reproducible controlled mutations to the human-supported natural rows:

- numeric/date shifts;
- event negation;
- quantifier flip;
- entity-status/ranking/stance/modality flip;
- quote-external publisher attribution.

Total: **317 rows**.

Label distribution:

- 104 entailed;
- 107 partially_entailed;
- 105 not_entailed;
- 1 unclear.

This set is useful for testing the whole semantic validation stack, but many of its synthetic errors would already be caught by the deterministic boundary validator. It must therefore **not** be used alone to claim incremental value for the third-layer judge.

## Post-boundary benchmark

File: `eval/benchmarks/evidence-entailment-post-boundary-v1.json`

This is the primary third-layer benchmark. It keeps the 135 natural rows and adds one semantic hard negative per natural supported row using transformations designed to avoid simple numeric/entity/source-metadata leakage:

- plan → actual;
- target → actual;
- uncertainty → certainty;
- stance / quantifier reversal;
- causal strengthening;
- scope expansion;
- exclusivity strengthening.

Total: **239 rows**.

Label distribution:

- 104 entailed;
- 91 partially_entailed;
- 43 not_entailed;
- 1 unclear.

Domain distribution:

- tech: 80;
- public: 68;
- sport: 51;
- finance: 40.

## Leakage-safe split

Splitting is by **case_id**, not by row.

Held-out test cases:

- tech: C03, C07;
- sport: C10, C13;
- public: C16, C19;
- finance: C22, C24.

All natural rows and all mutations from a forecasting case stay in the same split.

Post-boundary split:

- dev: 157 rows;
- test: 82 rows.

Held-out test labels:

- 34 entailed;
- 34 partially_entailed;
- 13 not_entailed;
- 1 unclear.

Thresholds/cascade parameters may be selected on dev only. The test split is evaluated once after configuration selection.

## Measured multilingual NLI baseline

Model: `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`, run on Tang GPU with a temporary research-only environment. It is independent from the DeepSeek 证据评估 generator.

### Broad 317-row set

The NLI baseline is not suitable as a standalone hard gate. At the strictest listed entailment threshold (0.95), it accepted 139/317 rows, but only **51.1%** of accepted rows were gold `entailed`; supported recall was **68.3%**.

The largest failure modes were numeric shifts and quote-external publisher attribution, which is expected to remain the responsibility of Boundary-v2 rather than NLI.

### Primary post-boundary 239-row set

Full-set 0.95 threshold:

- accepted strict precision: **70.3%**;
- supported recall: **68.3%**;
- false accepts: **30**.

Leakage-safe dev/test rescoring:

| Split | Threshold | Accepted precision | Supported recall | Accepted | False accepts |
| --- | ---: | ---: | ---: | ---: | ---: |
| dev | 0.95 | **68.1%** | 67.1% | 69 | 22 |
| held-out test | 0.95 | **75.0%** | 70.6% | 32 | 8 |

Therefore multilingual NLI alone fails the 95% precision / 90% recall production target. It remains useful as an **independent cascade signal**, especially for high-confidence contradiction or uncertainty routing.

## Primary gate metrics

For a production semantic gate, four-class accuracy is secondary.

The primary metric is:

```text
accepted strict precision =
human-gold entailed findings accepted by the judge
/
all findings accepted by the judge
```

Also report:

- supported recall;
- acceptance rate / coverage;
- false accepts;
- Wilson 95% interval for accepted strict precision;
- performance by hard-negative phenomenon.

Target for initial soft-gate consideration:

- accepted strict precision >= 95%;
- supported recall >= 90%.

A stronger 98% claim should require both a larger expert-labelled held-out set and a lower confidence bound consistent with the claim; a point estimate alone is not sufficient.

## Models

Three judge families are supported:

1. **same-model bootstrap LLM** — diagnostic only; not independent;
2. **multilingual NLI** — independent discriminative baseline;
3. **independent LLM** — different model family/provider from the DeepSeek 证据评估 generator.

The current local independent LLM path uses Qwen2.5 Instruct on the GPU test machine. No model weight or token is committed to the repository.

## Cascade policies

Two policies are implemented:

- `agreement`: accept only if NLI and LLM both give high-confidence entailment;
- `nli_then_llm`: NLI handles high-confidence entailment/contradiction, independent LLM handles the uncertain middle.

`eval/tune_entailment_cascade.py` searches thresholds on dev and evaluates the chosen configuration once on held-out test.
