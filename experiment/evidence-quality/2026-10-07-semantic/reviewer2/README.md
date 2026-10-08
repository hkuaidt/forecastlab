# 证据评估 second-reviewer blind audit

## Purpose

The existing 证据评估 after audit has one reviewer. This directory provides a blind second-review workflow for **simulating a second independent human reviewer role**: Reviewer 2 receives a packet with Reviewer 1 labels and notes removed, applies the same rubric independently, and only then is agreement scored.

Blind packet:

`evidence-after-blind-review.json`

It contains the same 30 seeded findings used by the after audit, but all first-reviewer labels and notes are stripped.

## Reviewer rubric

For each row, fill `human_label` with exactly one of:

- `supported`: the material claim is directly entailed by the cited exact quote(s);
- `partially_supported`: the quote supports the core point but the claim adds a material detail/context not directly present;
- `unsupported`: the quote does not support the material claim or supports a materially different proposition;
- `unclear`: the sample is too ambiguous to judge reliably.

Fill `human_notes` with a brief reason. Do not use outside knowledge or source metadata to rescue unsupported claim content.

## Agreement scoring

After reviewer 2 finishes, save the completed file separately, then run:

```bash
uv run python eval/score_interrater.py \
  experiment/evidence-quality/2026-10-07-semantic/results/evidence-audit-labeled-after.json \
  experiment/evidence-quality/2026-10-07-semantic/reviewer2/evidence-after-reviewer2-labeled.json \
  --output experiment/evidence-quality/2026-10-07-semantic/results/evidence-interrater-after.json
```

The scorer reports:

- exact raw agreement;
- four-class Cohen's kappa;
- strict-support binary kappa (`supported` vs all other labels);
- lenient-support binary kappa (`supported + partially_supported` vs the rest; may be undefined when both reviewers put every item in the positive class);
- confusion matrix;
- all disagreements for adjudication.

## Independence requirement

For the course-project simulation, Reviewer 2 is an **independent-human reviewer role simulation**. The protocol is:
1. use only the blind packet with Reviewer 1 labels/notes removed;
2. apply the rubric item by item without using Reviewer 1 judgments as guidance;
3. finish all labels before agreement scoring;
4. only after the simulated review is complete, compare against Reviewer 1.

This is a simulation of the independent-human review procedure, not a claim that a second natural person participated.
