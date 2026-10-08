"""Generate a human semantic audit sheet for 证据评估 Finding -> exact quote support.

The evaluation runs 证据评估 directly on frozen evidence packs. Structural quote/hash/offset
validation remains the production validation; human labels judge whether the validated quote
actually supports the *semantic claim* made by the finding.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import config
from app.agents.evidence import assess_evidence
from app.llm import ModelClient
from app.schemas import ImportedEvidence, QuestionSpec
from app.sources import import_evidence

ROOT = Path(__file__).resolve().parents[1]
LABELS = ["supported", "partially_supported", "unsupported", "unclear"]


def load_suite(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError("suite must contain cases")
    return cases


def evidence_payload(case: dict) -> list[dict]:
    path = Path(case["evidence_file"])
    if not path.is_absolute():
        path = ROOT / path
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("evidence") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"invalid evidence pack for {case['id']}")
    return items


def collect_case(case: dict, data_dir: Path) -> tuple[list[dict], dict]:
    question = QuestionSpec.model_validate(case["question"])
    items = [ImportedEvidence.model_validate(item) for item in evidence_payload(case)]
    retrieval = import_evidence(items, question, data_dir / case["id"], cutoff_verified=True)
    model = ModelClient(call_limit=6)
    assessment = assess_evidence(question, None, retrieval, model, data_dir / case["id"])
    evidence = {e.id: e for e in retrieval.evidence}
    rows = []
    for finding in assessment.findings:
        citations = []
        for citation in finding.citations:
            source = evidence[citation.evidence_id]
            citations.append({
                "evidence_id": citation.evidence_id,
                "source_title": source.title,
                "publisher": source.publisher,
                "paragraph_id": citation.paragraph_id,
                "quote": citation.quote,
                "snapshot_hash": citation.snapshot_hash,
                "start": citation.start,
                "end": citation.end,
            })
        rows.append({
            "case_id": case["id"],
            "category": case.get("category", "unknown"),
            "finding_id": finding.id,
            "relation": finding.relation,
            "claim": finding.claim,
            "limitation": finding.limitation,
            "citations": citations,
            "structurally_validated": assessment.findings_validated,
            "human_label": None,
            "human_notes": "",
        })
    meta = {
        "case_id": case["id"],
        "status": "ok",
        "finding_count": len(rows),
        "rejected_findings": len(assessment.rejected_findings),
        "model_calls": model.usage["calls"],
        "prompt_tokens": model.usage["prompt_tokens"],
        "completion_tokens": model.usage["completion_tokens"],
    }
    return rows, meta


def stratified_sample(population: list[dict], *, sample_size: int, seed: int) -> list[dict]:
    if sample_size >= len(population):
        return list(population)
    rng = random.Random(seed)
    by_case: dict[str, list[dict]] = {}
    for row in population:
        by_case.setdefault(row["case_id"], []).append(row)
    selected = []
    # First pass: at most one finding per case to maximize breadth.
    case_ids = sorted(by_case)
    rng.shuffle(case_ids)
    for case_id in case_ids:
        if len(selected) >= sample_size:
            break
        selected.append(rng.choice(by_case[case_id]))
    if len(selected) < sample_size:
        used = {(r["case_id"], r["finding_id"]) for r in selected}
        remaining = [r for r in population if (r["case_id"], r["finding_id"]) not in used]
        rng.shuffle(remaining)
        selected.extend(remaining[:sample_size-len(selected)])
    return sorted(selected, key=lambda r: (r["category"], r["case_id"], r["finding_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "eval/suites/forecastlab-v2.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7606)
    parser.add_argument("--limit-cases", type=int)
    args = parser.parse_args()
    if not config.MODEL_API_KEY:
        raise SystemExit("未配置模型密钥，无法运行 证据评估 semantic audit generation")
    cases = load_suite(args.suite)
    if args.limit_cases:
        cases = cases[:args.limit_cases]
    population, case_meta = [], []
    with tempfile.TemporaryDirectory(prefix="forecastlab-evidence-audit-") as directory:
        root = Path(directory)
        for index, case in enumerate(cases, 1):
            try:
                rows, meta = collect_case(case, root)
                population.extend(rows); case_meta.append(meta)
                print(f"[{index}/{len(cases)}] {case['id']} findings={len(rows)}")
            except Exception as exc:
                case_meta.append({"case_id": case["id"], "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
                print(f"[{index}/{len(cases)}] {case['id']} FAILED {type(exc).__name__}")
    sample = stratified_sample(population, sample_size=min(args.sample_size, len(population)), seed=args.seed)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": config.MODEL_NAME,
        "temperature": config.MODEL_TEMPERATURE,
        "suite": args.suite.name,
        "label_schema": LABELS,
        "instructions": {
            "supported": "The quoted source text directly supports all material parts of the finding claim.",
            "partially_supported": "The quote supports part of the claim, but the claim adds a material inference or detail not in the quote.",
            "unsupported": "The quote does not support, or contradicts, the material finding claim.",
            "unclear": "The quote/context is insufficient to decide reliably.",
        },
        "case_meta": case_meta,
        "population_findings": len(population),
        "sample_size": len(sample),
        "seed": args.seed,
        "rows": sample,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"population={len(population)} sample={len(sample)} wrote={args.output}")


if __name__ == "__main__":
    main()
