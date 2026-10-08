# 证据评估 exact-quote boundary v2 real rerun — 2026-10-08

## Setup

- Code under test: fork `main` after PR #5, merge commit `457e6c5b14d89decb9a883d1fa889af0dc10b41b`.
- Main push CI run #56: **success** (backend, frontend build, Chromium, browser E2E).
- Local targeted regression: **34 passed**, 2 dependency warnings.
- Model: DeepSeek `deepseek-flash`, temperature 0.
- Suite: `eval/suites/forecastlab-v2.json`.
- Cases completed: **24/24**.
- Structurally validated finding population: **52**.
- Seeded semantic sample: **30**, seed **7606**.
- Review protocol: two simulated independent-human reviewer roles, each applying the exact-quote rubric to the unlabeled packet independently.

## Results

| Metric | Boundary v1 | Boundary v2 |
| --- | ---: | ---: |
| Population findings | 53 | 52 |
| Reviewer 1 supported | 27/30 (90.0%) | **28/30 (93.33%)** |
| Reviewer 2 supported | 26/30 (86.67%) | **27/30 (90.0%)** |
| Reviewer 2 partial | 4 | **3** |
| Unsupported | 0 | **0** |
| Unclear | 0 | **0** |
| Lenient support | 100% | **100%** |
| Raw reviewer agreement | 96.67% | **96.67%** |
| Four-class Cohen's kappa | 0.83871 | **0.782609** |
| Strict-support kappa | 0.83871 | **0.782609** |

The lower v2 kappa despite the improved strict-support counts is a prevalence effect: both reviewers moved further toward the dominant `supported` class while retaining only one disagreement. Raw agreement remains 29/30.

## What v2 fixed

The four v1 residual patterns are no longer present in the v2 sample:

1. bare date/value row no longer becomes a **closing** value;
2. `Cavs` is no longer expanded to `Cleveland Cavaliers`;
3. quote-external `fixed commit` / `Expected` context is no longer promoted into the claim;
4. `official` status is no longer imported from source context.

The new validator either forces a conservative repair or excludes the candidate. C01 produced two rejected candidates during the real rerun, demonstrating the fail-closed path.

## Remaining strict-review partials

Reviewer 2 marks only three rows as partially supported:

1. **C16/F002** — the quote uses unresolved pronoun `They`; the claim calls the referents “议题”.
2. **C20/F002** — the quote says the summit will be **hosted in** Johannesburg, South Africa; the claim says it will be **hosted by South Africa**, assigning an agent.
3. **C09/F002** — the quote says Manchester City will **start their title defence**; the claim adds the explicit “开启新赛季” framing.

Reviewer 1 agrees on the first two and accepts the third as a direct paraphrase, producing the single inter-rater disagreement.

## Interpretation

Boundary v2 reaches the predefined target: the stricter second reviewer has **90.0% strict support**, with **0 unsupported**, **0 unclear**, and **100% lenient support**.

The remaining three cases are no longer metadata/date leakage. They are fine-grained semantic entailment questions involving pronoun antecedents, grammatical role (`in` vs `by`), and contextual paraphrase. Adding more hand-written lexical rules for these cases would risk overfitting the 30-row sample.

Therefore boundary v2 is the recommended stopping point for deterministic validation. Future work, if desired, should use a general entailment/judge layer or a larger expert-labeled corpus rather than more case-specific regex rules.

## Artifacts

- `results/evidence-audit-main-seed7606.json`
- `results/reviewer1-labeled.json`
- `results/reviewer2-labeled.json`
- `results/reviewer1-score.json`
- `results/reviewer2-score.json`
- `results/interrater.json`
