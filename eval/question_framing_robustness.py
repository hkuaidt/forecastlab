"""Paired neutral-vs-leading robustness evaluation for ForecastLab 问题分析.

This script evaluates framing behavior only. It never confirms premises and never starts
ForecastLab runs. All explicit date/rule fields are supplied by the frozen cases so a
blocking clarification is measurable rather than caused by missing test metadata.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import config
from app.question_service import QuestionService
from app.schemas import AnalyzeQuestionRequest, QuestionDraft
from app.storage import RunStore


EXPLICIT_FIELDS = ("as_of", "resolve_by", "resolution_rule", "resolution_source", "mode", "user_assumptions")


def premise_anchor_detected(frame, anchor: str | None) -> bool | None:
    if anchor is None:
        return None
    needle = anchor.strip()
    return any(
        needle in premise.original_span or premise.original_span in needle or needle in premise.content
        for premise in frame.premises
    )


def explicit_fields_preserved(input_question: QuestionDraft, frame) -> bool:
    proposed = frame.proposed_spec
    return all(getattr(input_question, field) == getattr(proposed, field) for field in EXPLICIT_FIELDS)


def evaluate_case(service: QuestionService, case: dict, *, repeat_index: int) -> dict:
    question = QuestionDraft.model_validate(case["question"])
    started = time.monotonic()
    request = AnalyzeQuestionRequest(question=question, operation_id=f"robust-{case['id']}-{repeat_index}-{uuid4().hex}")
    try:
        frame = service.analyze(request)
        blocking = [c for c in frame.clarifications if c.blocking and c.status == "open"]
        anchor = case.get("expected_premise_anchor")
        detected = premise_anchor_detected(frame, anchor)
        row = {
            "id": case["id"],
            "pair_id": case["pair_id"],
            "variant": case["variant"],
            "repeat": repeat_index,
            "status": "ok",
            "premise_count": len(frame.premises),
            "premises": [p.model_dump(mode="json") for p in frame.premises],
            "clarifications": [c.model_dump(mode="json") for c in frame.clarifications],
            "blocking_clarification_count": len(blocking),
            "explicit_fields_preserved": explicit_fields_preserved(question, frame),
            "expected_premise_anchor": anchor,
            "expected_premise_detected": detected,
            "neutral_false_positive": case["variant"] == "neutral" and bool(frame.premises),
            "ready_for_confirmation": frame.status == "ready_for_confirmation",
            "model": frame.analysis_record.model,
            "request_ids": frame.analysis_record.request_ids,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        return row
    except Exception as exc:
        return {
            "id": case["id"], "pair_id": case["pair_id"], "variant": case["variant"],
            "repeat": repeat_index, "status": "failed",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    neutral = [r for r in ok if r["variant"] == "neutral"]
    leading = [r for r in ok if r["variant"] == "leading"]
    pair_checks = []
    keys = sorted({(r["pair_id"], r["repeat"]) for r in ok})
    for pair_id, repeat in keys:
        n = next((r for r in neutral if r["pair_id"] == pair_id and r["repeat"] == repeat), None)
        l = next((r for r in leading if r["pair_id"] == pair_id and r["repeat"] == repeat), None)
        if n and l:
            pair_checks.append({
                "pair_id": pair_id, "repeat": repeat,
                "leading_adds_premise": l["premise_count"] > n["premise_count"],
                "both_preserve_explicit_fields": n["explicit_fields_preserved"] and l["explicit_fields_preserved"],
                "both_without_blocking_clarification": n["blocking_clarification_count"] == 0 and l["blocking_clarification_count"] == 0,
            })
    detected = [r for r in leading if r["expected_premise_detected"] is not None]
    return {
        "rows": len(rows),
        "succeeded": len(ok),
        "failed": len(rows) - len(ok),
        "neutral_cases": len(neutral),
        "leading_cases": len(leading),
        "neutral_false_positive_rate": (sum(r["neutral_false_positive"] for r in neutral) / len(neutral)) if neutral else None,
        "leading_premise_detection_rate": (sum(bool(r["expected_premise_detected"]) for r in detected) / len(detected)) if detected else None,
        "explicit_field_preservation_rate": (sum(r["explicit_fields_preserved"] for r in ok) / len(ok)) if ok else None,
        "blocking_clarification_rate": (sum(r["blocking_clarification_count"] > 0 for r in ok) / len(ok)) if ok else None,
        "ready_for_confirmation_rate": (sum(r["ready_for_confirmation"] for r in ok) / len(ok)) if ok else None,
        "paired_leading_adds_premise_rate": (sum(p["leading_adds_premise"] for p in pair_checks) / len(pair_checks)) if pair_checks else None,
        "pair_checks": pair_checks,
        "mean_elapsed_seconds": statistics.fmean(r["elapsed_seconds"] for r in ok) if ok else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be >= 1")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        parser.error("cases must be a non-empty JSON array")
    if not config.MODEL_API_KEY:
        raise SystemExit("未配置模型密钥，无法运行 live 问题分析 robustness evaluation")
    with tempfile.TemporaryDirectory(prefix="forecastlab-question-framing-robustness-") as directory:
        store = RunStore(Path(directory))
        service = QuestionService(store)
        rows = [evaluate_case(service, case, repeat_index=repeat)
                for repeat in range(1, args.repeat + 1) for case in cases]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": config.MODEL_NAME,
        "temperature": config.MODEL_TEMPERATURE,
        "repeat": args.repeat,
        "case_count": len(cases),
        "summary": summarize(rows),
        "rows": rows,
        "limitations": [
            "Premise detection is scored against frozen lexical anchors, not a full semantic gold annotation.",
            "Temperature 0 does not guarantee deterministic remote-model output.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
