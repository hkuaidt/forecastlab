# 证据评估 exact-quote boundary v1 real rerun — 2026-10-08

## Setup

- Code under test: fork `main` after PR #3/#4.
- Model: DeepSeek `deepseek-flash`, temperature 0.
- Suite: `eval/suites/forecastlab-v2.json`.
- All 24 frozen evidence cases completed.
- Population: **53 structurally validated findings**.
- Seeded audit sample: **30 findings**, seed **7606**.
- Reviewer protocol: two simulated independent-human reviewer roles. Each reviewer receives the same unlabeled 30-row packet and applies the four-label exact-quote rubric independently.

## Reviewer results

| Metric | Reviewer 1 | Reviewer 2 |
| --- | ---: | ---: |
| supported | **27/30 (90.0%)** | **26/30 (86.67%)** |
| partially supported | 3 | 4 |
| unsupported | 0 | 0 |
| unclear | 0 | 0 |
| lenient support | **100%** | **100%** |

Inter-rater:
- raw agreement: **96.67%**
- four-class Cohen's kappa: **0.83871**
- strict-support binary kappa: **0.83871**
- lenient-support kappa: undefined because every sample is lenient-positive.

The only inter-rater disagreement is C14/F002:
- Reviewer 1 accepts `Cavs` → `Cleveland Cavaliers` as a direct abbreviation expansion;
- Reviewer 2 treats the full entity name as quote-external context.

## Residual Reviewer-2 partials

1. **C22/F001** — quote is only `2025-06-30\t22679.010`; claim adds that it is a **closing** value.
2. **C14/F002** — quote says `Cavs`; claim expands to **Cleveland Cavaliers**.
3. **C01/F003** — quote gives the version/date line; claim adds **fixed commit** provenance and **Expected** heading context.
4. **C08/F001** — quote says `we unveiled our efforts...`; claim adds **official** status.

These are narrower than the pre-boundary failures: there are no unsupported or unclear findings, and all 30 are at least partially supported.

## Conclusion

The first deterministic boundary layer materially reduces source/date metadata leakage, but a strict quote-only reviewer still finds four residual semantic-expansion patterns. Boundary v2 therefore targets:
- semantic measurement labels such as `收盘`;
- English proper-name expansion inside Chinese claims;
- quote-external headings such as `Expected`;
- provenance qualifiers such as `固定提交`;
- source-status qualifiers such as `官方`.

Raw artifacts are stored in `results/`.
