# Tavily live retrieval-quality results — run 3

## Result

This is the first successful **real Tavily** run through ForecastLab production `retrieve_evidence()`. Earlier authentication-failure attempts are not treated as retrieval-quality results.

| Metric | Result |
| --- | ---: |
| Cases completed | 8/8 |
| Retrieval-task success | 100.0% |
| Mean query coverage | 100.0% |
| Selected evidence | 80 |
| Mean evidence / case | 10.0 |
| Body-content rate | 97.5% |
| Snippet-only rate | 2.5% |
| Date-metadata rate | 0.0% |
| Expected-authoritative-domain hit rate | 100.0% |
| Duplicate aliases merged/recorded | 15 |
| Possible-same-source pairs | 3 |
| Exclusions | 0 |

## Per-case diagnostics

| Case | Evidence | Domains | Body | Authority | Aliases | Similar pairs |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| T01-python-release | 10 | 3 | 100% | python.org | 3 | 0 |
| T02-node-lts | 10 | 1 | 100% | nodejs.org | 1 | 0 |
| T03-fed-rate | 10 | 1 | 100% | federalreserve.gov | 3 | 1 |
| T04-nasa-artemis | 10 | 2 | 100% | nasa.gov | 3 | 0 |
| T05-copernicus-climate | 10 | 1 | 100% | climate.copernicus.eu | 2 | 0 |
| T06-atp-ranking | 10 | 1 | 90% | atptour.com | 0 | 2 |
| T07-premier-league-table | 10 | 2 | 90% | premierleague.com | 3 | 0 |
| T08-china-cpi | 10 | 1 | 100% | stats.gov.cn | 0 | 0 |

## Interpretation

- The live-search path is operational: all 16 retrieval tasks succeeded and all eight cases retained evidence from both queries.
- Raw-content availability is strong (97.5%), so 证据评估 usually receives page body text rather than only search snippets.
- Every case hit its expected authoritative domain in this targeted suite.
- Date metadata is the main weakness: Tavily returned no usable publication/update/event date metadata in this run. Therefore current-web retrieval is suitable for live forecasting, but it does **not** establish historical cutoff availability. ForecastLab should continue requiring frozen/imported evidence for historical backtests.
- Source diversity is intentionally low in this suite because queries explicitly target authoritative domains. This run tests official-source retrieval reliability, not open-web diversity.
- Duplicate aliases and near-duplicate warnings show that the source-grouping layer is exercised in real retrieval, but manual relevance review is still needed before claiming semantic precision.

## Manual review status

A seeded 30-item relevance/source-quality review packet was generated locally. It is not yet human-labeled, so no semantic relevance precision is reported here.

## Security

The Tavily credential was injected from a local temporary file and is not stored in the repository or result files.
