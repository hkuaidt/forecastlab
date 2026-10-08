"""Q is a stored question locator; only actual active P IDs target premises."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from app.agents.evidence import assess_evidence, validate_findings
from app.model_context import model_messages
from app.schemas import AssessmentCandidate, ImportedEvidence, QuestionFraming, QuestionSpec
from app.sources import import_evidence


def package(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/evidence_question_id_misuse.json").read_text())
    question = QuestionSpec.model_validate(fixture["question"])
    body = "兼容测试已经完成。"
    retrieval = import_evidence([ImportedEvidence(file_id="target-identity", title="测试记录",
        excerpt=body, body=body)], question, tmp_path)
    return fixture, question, retrieval


def row(payload, targets):
    source = payload["evidence"][0]
    passage = source["passages"][0]
    return {"target_premise_ids":targets,"claim":passage["text"], "relation":"background",
        "citations":[{"evidence_id":source["id"], "snapshot_hash":source["snapshot_hash"],
        "paragraph_id":passage["paragraph_id"], "quote":passage["text"]}]}


def wire(role, payload):
    return json.loads(model_messages(role, payload, {}, "instructions")[1]["content"])


def test_actual_six_q_targets_remain_rejected_instead_of_becoming_premises(tmp_path):
    fixture, question, retrieval = package(tmp_path)
    assert fixture["premises"] == [] and question.id == "Q-64f59ae3"
    assert len(fixture["findings"]) == 6
    findings = [{k:v for k,v in finding.items() if k != "candidate_index"} for finding in fixture["findings"]]
    candidate = AssessmentCandidate(summary="原始Q误用", findings=findings)
    before = candidate.model_dump()
    accepted, rejected = validate_findings(candidate, None, retrieval.evidence,
        {e.id:e.passages for e in retrieval.evidence})
    assert accepted == [] and len(rejected) == 6
    assert all("不存在或被用户否认的前提" in item.reason for item in rejected)
    assert all(item.candidate["target_premise_ids"] == [question.id] for item in rejected)
    assert candidate.model_dump() == before


def test_no_p_wire_hides_q_and_bounded_retry_shows_actual_empty_target_list(tmp_path):
    _, question, retrieval = package(tmp_path)
    original = question.model_dump()
    class Model:
        calls = 0
        def complete(self, role, payload, schema, instructions, **kwargs):
            self.calls += 1
            before = deepcopy(payload)
            view = wire(role, payload)
            assert payload == before and payload["question"]["id"] == question.id
            assert "id" not in view["question"]
            assert question.id not in json.dumps(view)
            assert view["valid_target_premise_ids"] == []
            assert "Q是问题编号，不是前提" in instructions
            assert "finding和gap" in instructions
            assert wire("world", payload)["question"]["id"] == question.id
            if self.calls == 1:
                return schema.model_validate({"summary":"Q误填", "findings":[row(payload,[question.id])],
                    "gaps":[{"missing":"未取得采用规则。", "target_premise_ids":[question.id]}]})
            assert any("valid_target_premise_ids=[]" in reason for reason in payload["validation_feedback"])
            return schema.model_validate({"summary":"无P背景事实", "findings":[row(payload,[])]})
    model = Model()
    assessment = assess_evidence(question, None, retrieval, model, tmp_path)
    assert model.calls == 2 and len(assessment.findings) == 1
    assert assessment.findings[0].target_premise_ids == []
    assert len(assessment.rejected_findings) == 2
    assert assessment.rejected_findings[0].candidate["target_premise_ids"] == [question.id]
    assert assessment.rejected_findings[1].candidate["gap"]["target_premise_ids"] == [question.id]
    assert question.model_dump() == original


def test_active_p_targets_survive_projection_while_rejected_p_is_excluded(tmp_path, clear_framing):
    _, question, retrieval = package(tmp_path)
    frame = QuestionFraming.model_validate(clear_framing)
    frame.premises[0].user_review = "retained"
    frame.premises.append(frame.premises[0].model_copy(update={"id":"P002", "user_review":"rejected"}))
    class Model:
        calls = 0
        def complete(self, role, payload, schema, instructions, **kwargs):
            self.calls += 1
            view = wire(role, payload)
            assert view["valid_target_premise_ids"] == ["P001"]
            assert [p["id"] for p in view["question_framing"]["premises"]] == ["P001"]
            return schema.model_validate({"summary":"活动前提证据", "findings":[row(payload,["P001"])]})
    model = Model()
    assessment = assess_evidence(question, frame, retrieval, model, tmp_path)
    assert model.calls == 1 and assessment.findings[0].target_premise_ids == ["P001"]


@pytest.mark.parametrize("target", ["Q-unknown", "P999", "P002"])
def test_unknown_or_rejected_targets_are_never_silently_removed(tmp_path, clear_framing, target):
    _, question, retrieval = package(tmp_path)
    frame = QuestionFraming.model_validate(clear_framing)
    frame.premises.append(frame.premises[0].model_copy(update={"id":"P002", "user_review":"rejected"}))
    payload = {"evidence":[e.model_dump(mode="json") for e in retrieval.evidence]}
    candidate = AssessmentCandidate(summary="无效对象", findings=[row(payload,[target])])
    accepted, rejected = validate_findings(candidate, frame, retrieval.evidence,
        {e.id:e.passages for e in retrieval.evidence})
    assert not accepted and rejected[0].candidate["target_premise_ids"] == [target]
