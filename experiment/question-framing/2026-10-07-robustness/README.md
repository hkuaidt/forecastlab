# 问题分析与证据评估 robustness experiment

This directory contains the frozen outputs for the 2026-10-07 问题分析与证据评估 evaluation.

- `report.md`: interpretation, limitations, and reproduction commands.
- `results/question-framing-neutral-leading-v2-run1.json`: 8 paired v2 questions, 3 repeats (48 runs).
- `results/evidence-finding-quote-audit-labeled.json`: deterministic 30-Finding human semantic audit sample.
- `results/evidence-finding-quote-audit-score.json`: strict/lenient support rates.
- `results/analysis-summary.json`: compact machine-readable summary.

The earlier fictional-entity dataset is kept under `eval/cases/question-framing-neutral-leading-fictional-sanity.json` only as a test-design sanity artifact. It is not used for formal claims.

No API credentials are stored in this directory.
