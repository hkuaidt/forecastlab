"""Traceable fixture/live evaluation. No semantic scores are manufactured."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import config
from app.api import public_run_data
from app.question_evidence_demo import classroom_case, demo_materials
from app.agents.evidence import assess_evidence
from app.graph import execute
from app.llm import ModelClient
from app.question_service import QuestionService
from app.schemas import (AnalyzeQuestionRequest, ConfirmQuestionRequest, PremiseDecision, RunRecord,
                         ImportedEvidence, QuestionSpec)
from app.sources import import_evidence, retrieve_evidence
from app.storage import RunStore


def run_cases(cases: list[dict], *, mode: str = "fixture", data_dir: Path) -> dict:
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    store = RunStore(data_dir)
    service = QuestionService(store)
    rows = []
    for index, case in enumerate(cases):
        started = time.monotonic()
        row = {"id": case.get("id", str(index)), "input": case, "validation_mode": mode,
               "semantic_review": "not_performed", "status": "not_run", "request_ids": [], "model_calls": []}
        request = None
        try:
            if mode == "live" and not config.MODEL_API_KEY:
                row["reason"] = "未配置真实模型密钥；未发起付费调用"
                continue
            request = AnalyzeQuestionRequest(question=case["question"],
                demo_case_id=case.get("demo_case_id", "question-evidence-demo") if mode == "fixture" else None)
            frame = service.analyze(request)
            if frame.status == "needs_clarification" and (mode == "fixture" or case.get("answers")):
                answers = (case.get("answers") if mode == "live" else
                    [{"clarification_id": c.id, "answer": classroom_case()["allowed_answers"][0]} for c in frame.clarifications if c.status == "open"])
                frame = service.analyze(AnalyzeQuestionRequest(question=frame.proposed_spec, draft_id=frame.draft_id,
                    expected_revision=frame.revision, answers=answers, demo_case_id=frame.demo_case_id))
            row["question_framing"] = frame.model_dump(mode="json")
            row["model"] = frame.analysis_record.model
            row["prompt_version"] = frame.analysis_record.prompt_version
            if frame.status != "ready_for_confirmation" or (mode == "live" and frame.premises and not case.get("decisions")):
                row["status"] = "needs_user_confirmation"
                continue
            decisions = ([PremiseDecision.model_validate(d) for d in case.get("decisions", [])] if mode == "live" else
                         [PremiseDecision(premise_id=p.id, user_review="retained") for p in frame.premises])
            confirmation = service.confirm(frame.draft_id, ConfirmQuestionRequest(expected_revision=frame.revision, decisions=decisions))
            if mode == "fixture":
                record = RunRecord(run_id=f"eval_{index}_{frame.draft_id}", question=confirmation.question,
                    question_framing=confirmation.framing, confirmation_id=confirmation.confirmation_id,
                    question_origin="confirmed", evidence_mode="demo", demo=True, model="fixture",
                    retrieval_result=demo_materials(confirmation.question, confirmation.framing, data_dir))
                store.save(record); execute(record, record.retrieval_result.evidence, store)
                row["run"] = public_run_data(record)
                row["status"] = record.status
            else:
                if case.get("evidence_mode") == "online":
                    if not config.TAVILY_API_KEY:
                        row["status"] = "not_run"; row["reason"] = "证据阶段未执行：未配置检索密钥"; continue
                    retrieval = retrieve_evidence(confirmation.question, confirmation.framing.retrieval_plan, data_dir)
                else:
                    retrieval = import_evidence([ImportedEvidence.model_validate(e) for e in case.get("evidence", [])], confirmation.question, data_dir)
                prior = store.list_calls(frame.draft_id)
                model = ModelClient(call_limit=max(0, config.MAX_CALLS-len(prior)),
                    on_reserve=lambda h, v: store.reserve_call(frame.draft_id, "runtime", call_limit=max(0, config.MAX_CALLS-len(prior)), input_hash=h, prompt_version=v),
                    on_finish=store.finish_call)
                assessment = assess_evidence(confirmation.question, confirmation.framing, retrieval, model, data_dir)
                row["evidence_assessment"] = assessment.model_dump(mode="json")
                row["status"] = "analyzed"
            row["request_ids"] = [c.request_id for c in store.list_calls(frame.draft_id)]
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        finally:
            # All failures and unexecuted cases remain in the denominator.
            operation = store.get_operation(request.operation_id) if request is not None else None
            owner = row.get("question_framing", {}).get("draft_id") or (operation["draft_id"] if operation else None)
            if owner:
                calls = store.list_calls(owner)
                row["model_calls"] = [c.model_dump(mode="json") for c in calls]
                row["request_ids"] = [c.request_id for c in calls]
            row["elapsed_seconds"] = round(time.monotonic()-started, 6)
            rows.append(row)
    return {"validation_mode": mode, "semantic_review": "not_performed", "created_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(cases), "cases": rows,
            "limitations": ["固定响应只验证软件流程，不证明假设识别、反证质量或中性/引导性稳健性。", "人工语义评分未执行。"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        parser.error("cases 必须是数组")
    with tempfile.TemporaryDirectory(prefix="forecastlab-eval-") as directory:
        report = run_cases(cases, mode=args.mode, data_dir=Path(directory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{args.mode}: {len(report['cases'])} cases retained; semantic_review=not_performed")

if __name__ == "__main__":
    main()
