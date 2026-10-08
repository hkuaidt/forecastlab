"""Keep quote-checked findings; persist failed candidates before any bounded retry."""
from copy import deepcopy

import pytest
from app import graph
from app.agents.evidence import assess_evidence
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.schemas import ImportedEvidence, QuestionSpec, RetrievalResult, RunRecord
from app.sources import import_evidence
from app.storage import RunStore


def package(tmp_path):
    question = QuestionSpec(question="各测试结果会怎样影响项目后续决策？", mode="scenario")
    body = "\n".join(f"测试项目{number}已经完成。" for number in range(1, 9))
    retrieval = import_evidence([ImportedEvidence(file_id="progress", title="测试记录", excerpt=body, body=body)], question, tmp_path)
    return question, retrieval


def findings(payload):
    source = payload["evidence"][0]
    return [{"claim":p["text"], "relation":"background", "citations":[{
        "evidence_id":source["id"], "snapshot_hash":source["snapshot_hash"],
        "paragraph_id":p["paragraph_id"], "quote":p["text"]}]} for p in source["passages"]]


@pytest.mark.parametrize("damage", ["quote", "snapshot_hash"])
def test_seven_valid_one_tampered_finding_uses_one_call_and_keeps_audit(tmp_path, damage):
    question, retrieval = package(tmp_path)
    calls, progress = [], []
    class Model:
        def complete(self, role, payload, schema, instructions, **kwargs):
            calls.append(role)
            assert "先选择能独立支持" in instructions
            assert "来源的预测、作者观点或未来展望须写成" in instructions
            assert "无具体缺口时可留空" in instructions
            assert "上限，不是目标数量" in instructions
            rows = findings(payload)
            assert len(rows) == 8
            rows[-1]["citations"][0][damage] = "篡改"
            return schema.model_validate({"summary":"八条候选", "findings":rows})
    assessment = assess_evidence(question, None, retrieval, Model(), tmp_path, on_progress=progress.append)
    assert calls == ["evidence12"]
    assert len(assessment.findings) == 7 and assessment.findings_validated
    assert len(assessment.rejected_findings) == 1
    assert len(progress) == 1 and len(progress[0].findings) == 7
    assert progress[0].rejected_findings[0].reason
    assert all(c.quote != "篡改" for f in assessment.findings for c in f.citations)
    # The persisted progress object is detached from subsequent final gap updates.
    assert len(progress[0].gap_details) < len(assessment.gap_details)


@pytest.mark.parametrize("first", ["all_quotes_invalid", "parse_error"])
def test_no_valid_candidate_gets_at_most_two_calls_and_progress_precedes_retry(tmp_path, first):
    question, retrieval = package(tmp_path)
    events, snapshots = [], []
    def publish(assessment):
        events.append("progress")
        snapshots.append(assessment.model_dump(mode="json"))
    class Model:
        def complete(self, role, payload, schema, instructions, **kwargs):
            events.append("call")
            if events.count("call") == 1:
                if first == "parse_error":
                    raise ValueError("缺少findings结构")
                rows = findings(payload)
                for row in rows:
                    row["citations"][0]["quote"] = "篡改"
                return schema.model_validate({"summary":"拒绝候选", "findings":rows})
            assert events[:3] == ["call", "progress", "call"]
            assert snapshots[0]["rejected_findings"]
            assert payload["validation_feedback"]
            return schema.model_validate({"summary":"修正后的候选", "findings":findings(payload)})
    assessment = assess_evidence(question, None, retrieval, Model(), tmp_path, on_progress=publish)
    assert events == ["call", "progress", "call", "progress"]
    assert len(assessment.findings) == 8
    assert len(assessment.rejected_findings) == (1 if first == "parse_error" else 8)
    assert snapshots[0]["findings"] == []


def test_two_rejected_responses_never_trigger_a_third_call(tmp_path):
    question, retrieval = package(tmp_path)
    calls = []
    class Model:
        def complete(self, role, payload, schema, instructions, **kwargs):
            calls.append(role)
            rows = findings(payload)
            for row in rows:
                row["citations"][0]["snapshot_hash"] = "wrong"
            return schema.model_validate({"summary":"拒绝候选", "findings":rows})
    assessment = assess_evidence(question, None, retrieval, Model(), tmp_path)
    assert len(calls) == 2 and assessment.findings == []
    assert len(assessment.rejected_findings) == 16


def test_graph_persists_partial_audit_before_repair_without_completing_stage(tmp_path):
    store = RunStore(tmp_path)
    record = RunRecord(run_id="evidence-progress", question=DEMO_QUESTION, evidence_mode="import",
                       model="fixture", status="running", stage="evidence",
                       retrieval_result=RetrievalResult(evidence=demo_evidence()))
    store.save(record)
    evidence_calls = []
    class Model:
        def complete(self, role, payload, schema, instructions, **kwargs):
            if role == "evidence12":
                evidence_calls.append(role)
                if len(evidence_calls) == 2:
                    saved = store.get(record.run_id)
                    assert saved.stage == "evidence" and "evidence" not in saved.stage_outputs
                    assert saved.evidence_assessment.rejected_findings
                    assert saved.evidence and saved.evidence[0].snapshot_hash
                rows = findings(payload)[:1]
                if len(evidence_calls) == 1:
                    rows[0]["citations"][0]["quote"] = "未提供引文"
                return schema.model_validate({"summary":"进度", "findings":rows})
            return schema.model_validate(demo_output(role, payload.get("actor",{}).get("id"), payload.get("round",1)))
    result = graph.build_graph(record, [], Model(), tmp_path, store=store).invoke({
        "question":DEMO_QUESTION.model_dump(mode="json")})
    assert len(evidence_calls) == 2 and result["forecast"]["status"] == "completed"
    saved = store.get(record.run_id)
    assert saved.evidence_assessment.findings and saved.evidence_assessment.rejected_findings
