# Live web full end-to-end evaluation — 2026-10-07

## Scope

This experiment tests the **actual ForecastLab HTTP product path** with a real model and real online retrieval. It does not call internal agent functions directly.

Flow under test:

```text
HTTP question submission
→ 问题分析 framing
→ user-style confirmation
→ live Tavily retrieval
→ 证据评估 evidence assessment
→ world model
→ actor/environment simulation
→ review
→ forecast
→ persisted run + citation replay
```

Model: DeepSeek `deepseek-flash`, temperature 0. Retrieval: Tavily production `retrieve_evidence()`.

The three prospective cases were unresolved at the run timestamp and cover technology, sports, and a public-space program:

- L01: Python 3.15 stable release by 2026-11-15;
- L02: Arsenal top four in the final 2026/27 Premier League table;
- L03: Artemis III crewed lunar landing completion by 2027-12-31.

No outcome accuracy is reported because these events have not yet resolved.

## What the live test found and repaired

The live path exposed issues that frozen-evidence backtests did not reveal:

1. **Near-cutoff retrieval semantics.** Live search necessarily stores a snapshot seconds after the user's `as_of`. Treating any `retrieved_at > as_of` as historically unavailable caused valid current-web evidence to be rejected. The repair introduces a bounded, auditable `live_near_cutoff` state (default 900 seconds) only for current live retrieval. Historical backtests remain strict and still require frozen/verified evidence.
2. **Future outcome mistaken for an evidence gap.** Phrases such as “final league table/final ranking” were expanded into the future-outcome filter so the system does not demand the prediction target itself as current evidence.
3. **Invalid Review locator IDs.** A real Review output used `R002` as an `affected_id`. Retrieval-task IDs are not trusted trace nodes. The system now drops invalid locator IDs, records that removal in the review explanation, and keeps the review prose instead of aborting the whole run.
4. **Repeated source-date gaps.** Ten live sources without publication metadata previously produced ten duplicate gaps. These are now aggregated into one explicit source-metadata limitation.
5. **证据评估 live-page output exhaustion.** Two first repaired live runs hit the evidence-assessment completion limit. Full snapshots are still stored server-side, while 证据评估 now receives at most 1400 selected passage characters per source, with tighter output guidance and a bounded completion budget.
6. **Server-owned world IDs.** Model-generated actor/assumption IDs are canonicalized to stable `A###` / `H###` namespaces before downstream simulation and review.

## Final run (run 4)

| Case | Status | Evidence | Valid findings | Exact citations checked | Actors | Sim steps | Probability basis | Probability |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| L01-python315 | completed | 10 | 10 | 15 | 1 | 2 | evidence_only | 是 90% / 否 10% |
| L02-arsenal-top4 | completed | 10 | 12 | 17 | 0 | 0 | evidence_only | 是 72% / 否 28% |
| L03-artemis3 | completed | 10 | 13 | 13 | 3 | 2 | evidence_only | 是 5% / 否 95% |

Aggregate:

- completed: **3/3**;
- hard failures: **0**;
- exact citations checked: **45**, failures: **0**;
- final invalid references: **0**;
- validated 证据评估 findings: **35**;
- model calls: **28**;
- prompt tokens: **626,886**;
- completion tokens: **31,793**;
- summed wall-clock case time: **148.5s**.

All three final runs had `findings_validated=true` and zero rejected findings.

## Important negative result

All three live cases reached the full World/Simulation/Review stages, but the full simulated branch was **blocked by Review**. The final probabilities therefore used `probability_basis=evidence_only`.

This is intentional fail-closed behavior, not a crash: Review found unsupported or insufficiently grounded simulation/world claims, while the separate evidence-only audit judged that the external evidence was sufficient for a cautious subjective probability.

Therefore this live evaluation supports the following narrower claim:

> ForecastLab can run the full online pipeline end to end while preserving citation integrity and falling back to evidence-only forecasting when the simulated branch is not sufficiently grounded.

It does **not** support the claim that the multi-agent simulation improves forecast accuracy. This is consistent with the historical v2 experiment, where `Single Agent + same evidence` was not worse than the full multi-agent pipeline on matched cases.

## Remaining limitations

- Tavily returned weak publication/update-date metadata in the live retrieval benchmark. The `live_near_cutoff` state is only for near-real-time runs and must never be used to assert historical availability.
- These three future cases cannot yet be scored for forecasting accuracy or calibration.
- The full simulated branch was blocked in all three live cases; its value currently lies in structured scenario generation and audit rather than demonstrated probability improvement.
- Live model usage remains expensive (about 626,886 prompt tokens across three cases). Cost/context efficiency remains an engineering improvement opportunity.
- A second independent human reviewer has not yet completed the 证据评估 blind audit, so inter-rater reliability is still pending.

## Reproduction

The HTTP harness is `eval/live_e2e_http.py`. The final structured run summary is `results/live-e2e-run4.json`. Credentials are injected locally and are not stored in this experiment directory or in Git.
