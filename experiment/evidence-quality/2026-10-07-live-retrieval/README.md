# Tavily live retrieval-quality evaluation

## Scope

This experiment evaluates ForecastLab's **production Tavily retrieval path** (`backend/app/sources.py::retrieve_evidence`). It does not replace that function with a benchmark-only search wrapper and does not call the forecasting LLM.

Suite: `eval/suites/tavily-quality-v1.json`.

The eight live-web cases cover technology, finance, public information, and sports. Each case contains two retrieval tasks and one expected authoritative domain. `as_of` is set to the actual run time because ForecastLab intentionally rejects today's web as evidence for historical cutoffs.

## Automatic metrics

The evaluator records:

- case status and evidence availability;
- retrieval-task success / empty / failure rates;
- selected-evidence query coverage;
- body-content rate and snippet-only rate;
- date-metadata coverage;
- unique-domain and source-group diversity;
- duplicate aliases and possible-same-source warnings;
- exclusions;
- expected authoritative-domain hit / recall.

These metrics test the search/retrieval system, not semantic correctness of every result.

## Human relevance sample

The evaluator can create a seeded blind sample of retrieved evidence. A reviewer labels:

- `relevance_label`: `relevant`, `partially_relevant`, `irrelevant`, `unclear`;
- `source_quality_label`: `primary_authoritative`, `reputable_secondary`, `other`, `unclear`.

The reviewer must judge only the displayed retrieved item; unseen page content must not be assumed.

## Run

```bash
uv run python eval/tavily_quality.py \
  --suite eval/suites/tavily-quality-v1.json \
  --data-dir /tmp/forecastlab-tavily-quality \
  --output experiment/evidence-quality/2026-10-07-live-retrieval/results/tavily-quality-run1.json \
  --review-output experiment/evidence-quality/2026-10-07-live-retrieval/results/tavily-quality-review-run1.json \
  --sample-size 30 \
  --seed 7606
```

`TAVILY_API_KEY` must be supplied through the environment or a local `.env`. The evaluator never accepts credentials on the command line and never serializes them into results.

## Status

Tooling and suite are implemented, and the first successful real run is recorded in `report.md` and `results/tavily-quality-run3-summary.json`. Live-index results may change between runs, so every result records its execution timestamp and should not be described as deterministic.
