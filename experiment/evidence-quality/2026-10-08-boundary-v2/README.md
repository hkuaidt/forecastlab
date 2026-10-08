# 证据评估 exact-quote boundary v2 evaluation — 2026-10-08

This directory will contain the post-v2 real-model rerun.

Protocol is fixed before observing v2 outputs:

- model: DeepSeek `deepseek-flash`
- temperature: 0
- suite: `eval/suites/forecastlab-v2.json`
- cases: all 24
- sample size: 30 findings
- sample seed: 7606
- reviewers: two simulated independent-human reviewer roles
- semantic labels: supported / partially_supported / unsupported / unclear
- primary metric: strict support rate
- secondary metrics: lenient support, raw inter-rater agreement, four-class Cohen's kappa, strict-support kappa

The v2 rerun must use code merged after the boundary-v2 PR passes full CI.
