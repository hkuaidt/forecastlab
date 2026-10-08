# 问题分析与证据评估 semantic robustness before/after — 2026-10-07

## Scope

This experiment evaluates 问题分析 neutral-vs-leading robustness and 证据评估 semantic support from exact quotes to findings. Model: DeepSeek `deepseek-flash`, temperature 0.

## 问题分析 — real ForecastLab-v2 paired set

Eight neutral/leading pairs were run twice (32 framing runs).

| Metric | Result |
| --- | ---: |
| Runs succeeded | 32/32 |
| Leading premise anchor detected | 100% |
| Explicit fields preserved | 100% |
| Neutral runs containing at least one premise | 81.25% |
| Runs with blocking clarification | 75% |
| Ready for confirmation | 25% |
| Leading member has more premises than neutral | 43.75% |

Inspection shows many neutral premises are not world-state assumptions but restatements of the research object, resolution criterion, time cutoff, comparison dates, or binary-decision wording.

## 问题分析 — fictional sanity set

A second eight-pair suite removes dependence on real historical facts and adds exactly one explicit antecedent only in the leading member. It was also run twice.

| Metric | Result |
| --- | ---: |
| Runs succeeded | 32/32 |
| Leading premise anchor detected | 100% |
| Explicit fields preserved | 100% |
| Neutral runs containing at least one premise | 81.25% |
| Runs with blocking clarification | 100% |
| Ready for confirmation | 0% |
| Leading member has more premises than neutral | 56.25% |

The same neutral-premise rate confirms a real semantic-boundary problem: 问题分析 frequently serializes question scope/identity/time/resolution fields as premises. Blocking clarifications are also over-produced, often reopening fields already supplied by `resolve_by`, `resolution_rule`, or `resolution_source`.

## 证据评估 — finding to exact-quote semantic audit

证据评估 was run directly on all 24 ForecastLab-v2 evidence packs. It produced 50 structurally validated findings. Seed 7606 selected 30 findings for a single-reviewer semantic audit.

| Label | Count |
| --- | ---: |
| supported | 15 |
| partially_supported | 15 |
| unsupported | 0 |
| unclear | 0 |

Strict support rate: **50%**. Lenient support rate (supported + partial): **100%**.

Common partial-support patterns include: turning a draw announcement into a team-specific path claim; turning `late 2024` into support for an October window; converting planned availability into a stronger no-delivery statement; and inferring non-release from a roadmap/title that only says `plans`.

## Conclusions

- 问题分析 preserves explicit user-owned fields and detects explicit leading premises, but its premise boundary is too broad and its clarification policy too aggressive.
- 证据评估 structural provenance is effective at preventing unrelated/fabricated quotes in this sample, but exact-quote existence is not enough to guarantee semantic entailment.
- The next repair should narrow 问题分析 premises to falsifiable world-state/causal assumptions and require 证据评估 finding claims to be conservative paraphrases entailed by their cited text.

## Limitations

- 问题分析 leading detection uses frozen lexical anchors, not a complete expert gold annotation of every possible hidden assumption.
- The neutral-premise metric is diagnostic: not every neutral extracted premise is necessarily harmful.
- 证据评估 has one human reviewer; inter-rater reliability is not available.
- The 30 findings are a seeded sample from one model run.
- Temperature 0 does not guarantee deterministic remote-model output.

## Repair

Repair commit: `8b51f85b7ff22e0cdf90bfec96a654bfa3f2b19c`.

问题分析 changes:

- prompt explicitly separates falsifiable background/world-state/causal assumptions from question schema fields;
- deterministic post-processing removes obvious research-object, target, cutoff, comparison-date, resolution-rule/source and binary-mode restatements;
- retrieval-plan premise indexes are remapped after filtering, so a removed scope-restatement cannot silently retarget another premise;
- already supplied `resolve_by`, `resolution_rule`, and `resolution_source` are treated as user-owned question definition and no longer reopened by default.

证据评估 changes:

- finding claims must be conservative paraphrases directly entailed by their exact quote(s);
- dates, causal interpretations, question-specific use, negative inference from absence, and stronger conclusions not present in the quote must move to limitation/summary rather than claim;
- titles/snippets may only support their literal content.

No result below is inferred from the prompt text alone; all after metrics come from new DeepSeek API runs.

## 问题分析 after — ForecastLab-v2 paired set

The same eight neutral/leading pairs were run twice again (32 new framing runs).

A refined metric distinguishes a legitimate factual benchmark embedded in a neutral question from an unexpected premise. C02 explicitly states the 2026-06-30 benchmark value `2207.86`; retaining that factual value for verification is allowed and is not counted as a false positive.

| Metric | Before | After |
| --- | ---: | ---: |
| Runs succeeded | 32/32 | 32/32 |
| Leading premise anchor detected | 100% | **100%** |
| Explicit fields preserved | 100% | **100%** |
| Neutral runs with any premise (raw diagnostic) | 81.25% | 12.5% |
| Neutral runs with an **unexpected** premise | 81.25% | **0%** |
| Runs with blocking clarification | 75% | **0%** |
| Ready for confirmation | 25% | **100%** |
| Leading member has more premises than neutral | 43.75% | **100%** |

The only neutral premise left after repair is the explicit C02 factual benchmark `2026-06-30 科创50收盘点位为 2207.86 点`, which is an appropriate item to verify.

## 问题分析 after — fictional sanity set

The independent fictional set was also rerun twice (32 new framing runs). Its neutral members contain only research targets/rules; each leading member adds exactly one antecedent.

| Metric | Before | After |
| --- | ---: | ---: |
| Runs succeeded | 32/32 | 32/32 |
| Leading premise anchor detected | 100% | **100%** |
| Explicit fields preserved | 100% | **100%** |
| Neutral runs with an unexpected premise | 81.25% | **0%** |
| Runs with blocking clarification | 100% | **0%** |
| Ready for confirmation | 0% | **100%** |
| Leading member has more premises than neutral | 56.25% | **100%** |

This second set is important because it shows the improvement is not limited to the historical ForecastLab-v2 wording.

## 证据评估 after — finding to exact-quote semantic audit

证据评估 was rerun directly on all 24 ForecastLab-v2 evidence packs after the conservative-claim repair.

- structurally validated findings in the new population: 53;
- new seeded sample: 30 findings, seed 7607;
- same four-label single-reviewer protocol as before;
- reviewer compared `finding.claim` only against its cited exact quote(s); source context was not used to rescue materially unsupported claim content.

| Label | Before (seed 7606) | After (seed 7607) |
| --- | ---: | ---: |
| supported | 15 | **27** |
| partially_supported | 15 | **3** |
| unsupported | 0 | 0 |
| unclear | 0 | 0 |
| strict support rate | 50% | **90%** |
| lenient support rate | 100% | **100%** |

The three remaining partial-support cases are substantially narrower than the old failures. They mainly add source/date/season/ranking context that is available in metadata but not literally present inside the selected quote. The old substantive overclaims—team-specific path claims, October-window inference from `late 2024`, delivery conclusions from planned availability, or non-release inference from roadmap titles—were not observed in the new sample.

Because the before and after samples use different deterministic seeds and one model run each, the 50% -> 90% change is evidence of a strong improvement trend, not a paired statistical estimate. No inter-rater reliability is available.

## Consolidated conclusions after repair

问题分析:

- preserves explicit user-owned fields: 100%;
- detects all frozen leading premises: 100%;
- unexpected neutral-premise rate: 0% on both the real-v2 adjusted metric and the fictional sanity set;
- blocking clarification rate fell to 0% on both after sets;
- all 64 after framing runs were ready for confirmation.

证据评估:

- structural quote/hash/paragraph validation still produced zero unrelated/fabricated-quote findings in the sampled audits;
- strict semantic support improved from 50% to 90% on an independent 30-finding after sample;
- lenient support remained 100%;
- remaining errors are mild context expansion rather than unsupported event conclusions.

These results support the intended 问题分析与证据评估 design much more strongly than the baseline, while still leaving two limitations: the 证据评估 semantic audit has only one reviewer, and remote-model output remains nondeterministic even at temperature 0.

## Final full-pipeline consistency run after semantic repair

The final semantic-boundary code was also rerun through the complete 24-case ForecastLab-v2 Full pipeline before opening a PR. This is a consistency/safety check: 问题分析 premise filtering does not affect the legacy-direct full-arm benchmark, while the stricter 证据评估 claim prompt can change downstream evidence assessments.

Code under test includes semantic repair commit `8b51f85b7ff22e0cdf90bfec96a654bfa3f2b19c` and evaluation-result commit `0e8b2149716d8e72e069927aa060ab4766a5ab5b`.

| Metric | Final semantic Full |
| --- | ---: |
| Completed with probability | 19/24 |
| Coverage | 79.17% |
| Hard failures | **0** |
| Brier on answered cases | 0.1448 |
| Brier with 0.5 abstention fallback | 0.1667 |
| Model calls | 179 |
| Tokens | 643,719 |
| Model seconds | 487.3 |

Five cases abstained as `insufficient_evidence`; none failed structurally.

### Comparison to frozen baselines

Using the previously frozen temperature-0 v2 baseline probabilities:

| Arm | Coverage | Brier |
| --- | ---: | ---: |
| Final Full | 79.17% | 0.1448 on answered / 0.1667 with fallback |
| Single Agent + same evidence | 100% | **0.1443** |
| Single Agent without evidence | 100% | 0.1881 |

On the exact 19 cases answered by Final Full:

| Arm | Matched 19-case Brier |
| --- | ---: |
| Final Full | 0.1448 |
| Single Agent + same evidence | **0.1265** |
| Single Agent without evidence | 0.1820 |

Therefore the semantic repairs should be claimed for **question framing and evidence semantic faithfulness**, not as a forecasting-accuracy improvement. They improve the correctness of intermediate representations while the current World/Actor/Simulation stack still does not demonstrate an accuracy advantage over the simpler evidence-conditioned baseline.

This result strengthens, rather than weakens, the final project evaluation: the system can report a real negative result about multi-stage simulation while separately demonstrating measurable improvements in 问题分析与证据评估 reliability.
