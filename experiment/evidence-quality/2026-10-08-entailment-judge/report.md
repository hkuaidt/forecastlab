# 证据评估 semantic entailment judge bootstrap — 2026-10-08

## Goal

Test whether a general semantic entailment judge can improve the current boundary-v2 strict-support result before enabling any production gate.

Input:
- boundary-v2 real DeepSeek audit;
- 30-row sample, seed 7606;
- strict human-role reference: Reviewer 2 = 27 supported / 3 partially_supported.

## Harness

The new judge sees only:
- `claim`
- exact quote(s)

It does not see:
- source title;
- publisher;
- URL;
- question answer;
- human labels.

Output:
- entailed / partially_entailed / not_entailed / unclear;
- confidence;
- unsupported spans;
- rationale.

## Bootstrap experiment: same model as generator

Judge model: DeepSeek `deepseek-flash`.

This run was deliberately marked:

- `same_as_generator_model=true`
- `independent_model=false`

because it is a bootstrap diagnostic, not an independent reliability result.

### Result

The judge labelled **30/30 findings as entailed**.

Against the stricter Reviewer 2 labels:

| Metric | Result |
| --- | ---: |
| Human supported | 27 |
| Human non-strict (partial) | 3 |
| Supported accepted | 27 |
| Non-strict blocked | **0** |
| Non-strict leaked | **3** |
| Supported blocked | 0 |
| Accept precision | **90.0%** |
| Supported recall | 100% |
| Non-strict specificity | **0%** |

Confidence thresholding did not help:

- threshold 0.50 → 30 accepted → 90% strict;
- threshold 0.70 → 30 accepted → 90% strict;
- threshold 0.80 → 30 accepted → 90% strict;
- threshold 0.90 → 30 accepted → 90% strict;
- threshold 0.95 → 30 accepted → 90% strict.

The same-model judge therefore provides **no improvement over boundary-v2 alone**.

## Interpretation

This is a useful negative result.

A third semantic layer is still the correct architectural direction, but the judge must be meaningfully independent from the generator. Re-prompting the same generator as a judge does not catch the remaining subtle entailment errors in this sample.

The three missed partials are exactly the cases the stricter human-role reviewer identified:

1. C09/F002 — `start title defence` expanded into explicit “new season” context;
2. C16/F002 — unresolved `They` antecedent converted into “these issues”;
3. C20/F002 — `hosted in` interpreted as South Africa being the hosting agent.

## Next experiments

Two independent routes are now implemented/planned:

1. **Independent LLM judge**
   - configure `ENTAILMENT_JUDGE_API_KEY`, `ENTAILMENT_JUDGE_BASE_URL`, `ENTAILMENT_JUDGE_MODEL`;
   - use a different model/provider family from the 证据评估 generator;
   - calibrate with `eval/score_entailment_judge.py`.

2. **Multilingual NLI baseline**
   - optional research script: `eval/evidence_nli_judge.py`;
   - default configurable model: `MoritzLaurer/ernie-m-large-mnli-xnli`;
   - lazy torch/transformers dependency, not part of the production runtime;
   - use the same calibration scorer to measure precision/coverage.

## Production gate criteria

Do not enable a hard semantic gate until a held-out calibration shows:

- accept precision ≥95% initially, preferably ≥98% on a larger set;
- near-zero false accepts of unsupported findings;
- supported recall preferably ≥90%;
- failure is fail-closed, never silent accept.

The current 30-row audit contains only three non-strict rows, so it is too small to support a strong 98% claim. A larger labelled set (roughly 100–200 diverse findings) should be built before a production reliability claim.
