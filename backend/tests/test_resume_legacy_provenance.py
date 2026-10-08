from copy import deepcopy
from pathlib import Path

import pytest

from app import graph as G
from app.agents.evidence import assess_evidence
from app.resume import restore_legacy_evidence_stage
from app.schemas import (ImportedEvidence, QuestionDraft, QuestionFraming, QuestionSpec,
                         RunRecord)
from app.sources import import_evidence
from app.storage import RunStore


class ResumeModel:
    def __init__(self, **kwargs):
        self.roles = []
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.actual_model = "fixture"

    def complete(self, role, payload, schema, instructions, **kwargs):
        self.roles.append(role)
        self.usage["calls"] += 1
        if role == "evidence12":
            e = payload["evidence"][0]
            p = e["passages"][0]
            body = {"summary": "已校验原文", "findings": [{
                "claim": p["text"], "relation": "background", "citations": [{
                    "evidence_id": e["id"], "snapshot_hash": e["snapshot_hash"],
                    "paragraph_id": p["paragraph_id"], "quote": p["text"]}]}]}
        elif role == "world":
            body = {"summary": "条件情景", "actors": [], "evidence_refs": ["E001"]}
        elif role == "review":
            body = {"status": "passed"}
        elif role == "forecast":
            body = {"status": "completed", "conclusion": "可继续核查。",
                    "probabilities": {"推进": 0.5, "延后": 0.3, "受限": 0.2}, "supporting": [{"text": "测试已经完成。",
                    "evidence_ids": ["E001"]}]}
        else:
            raise AssertionError(role)
        return schema.model_validate(body)


def old_record(tmp_path):
    question = QuestionSpec(question="项目后续发布会如何演变？", mode="scenario")
    retrieval = import_evidence([ImportedEvidence(file_id="old-source", title="测试原文",
        excerpt="测试😀已经完成。")], question, tmp_path)
    assessment = assess_evidence(question, None, retrieval, ResumeModel(), tmp_path)
    assert assessment.findings_validated
    raw = assessment.model_dump(mode="json")
    raw.pop("findings_validated")
    raw.pop("quality_profile")
    evidence = [e.model_dump(mode="json") for e in retrieval.evidence]
    return RunRecord.model_validate({
        "run_id": "resume_legacy", "question": question.model_dump(mode="json"),
        "evidence_mode": "import", "model": "fixture", "status": "interrupted",
        "stage": "world", "question_framing": QuestionFraming(
            draft_id="old_draft", revision=1, raw_question=question.question,
            proposed_spec=QuestionDraft.model_validate(question.model_dump()),
            status="ready_for_confirmation").model_dump(mode="json"),
        "evidence": evidence, "evidence_assessment": raw,
        "stage_outputs": {"question": {"question_analysis": {
            "normalized_question": question.question, "search_queries": []}},
            "evidence": {"evidence": evidence, "evidence_assessment": raw}}})


def test_old_registered_quotes_resume_without_repeating_model_or_search(tmp_path, monkeypatch):
    record = old_record(tmp_path)
    model = ResumeModel()
    monkeypatch.setattr(G, "ModelClient", lambda **kwargs: model)
    G.execute(record, record.evidence, RunStore(tmp_path), resume=True)
    assert record.status == "completed", record.errors
    assert model.roles == ["world", "review", "forecast"]
    saved = RunStore(tmp_path).get(record.run_id)
    assert saved.evidence_assessment.findings_validated
    assert saved.stage_outputs["evidence"]["evidence_assessment"]["findings_validated"]
    assert saved.evidence_assessment.quality_profile.validated_finding_count == 1
    assert not restore_legacy_evidence_stage(saved, tmp_path)


@pytest.mark.parametrize("damage", ["quote", "offset", "hash", "source", "passage", "snapshot"])
def test_old_quotes_cannot_be_certified_when_provenance_is_invalid(tmp_path, damage):
    record = old_record(tmp_path)
    stage = record.stage_outputs["evidence"]
    citation = stage["evidence_assessment"]["findings"][0]["citations"][0]
    if damage == "quote":
        citation["quote"] = "原文没有的陈述"
    elif damage == "offset":
        citation["start"] += 1
    elif damage == "hash":
        citation["snapshot_hash"] = "b" * 64
    elif damage == "source":
        citation["evidence_id"] = "E999"
    elif damage == "passage":
        stage["evidence"][0]["passages"][0]["text"] = "伪造段落"
    else:
        snapshot = tmp_path / stage["evidence"][0]["snapshot_path"]
        snapshot.write_text("tampered", encoding="utf-8")
    before = deepcopy(record.model_dump(mode="json"))
    with pytest.raises(ValueError, match="重新核验失败"):
        restore_legacy_evidence_stage(record, tmp_path)
    assert record.model_dump(mode="json") == before


def test_current_rejected_findings_are_not_migrated(tmp_path):
    record = old_record(tmp_path)
    record.stage_outputs["evidence"]["evidence_assessment"]["findings_validated"] = False
    before = record.model_dump(mode="json")
    assert not restore_legacy_evidence_stage(record, tmp_path)
    assert record.model_dump(mode="json") == before


def test_missing_snapshot_fails_before_any_model_call_and_keeps_old_stage(tmp_path, monkeypatch):
    record = old_record(tmp_path)
    record.stage_outputs["evidence"]["evidence"][0]["snapshot_path"] = None
    before = deepcopy(record.stage_outputs)
    model = ResumeModel()
    monkeypatch.setattr(G, "ModelClient", lambda **kwargs: model)
    store = RunStore(tmp_path)
    G.execute(record, record.evidence, store, resume=True)
    assert record.status == "failed"
    assert record.failed_stage == "evidence"
    assert "重新导入原文" in record.errors[-1]
    assert model.roles == []
    assert store.get(record.run_id).stage_outputs == before


@pytest.mark.parametrize("injection", ["number", "publisher", "title"])
def test_legacy_claim_cannot_add_facts_outside_its_registered_quote(tmp_path, injection):
    record = old_record(tmp_path)
    stage = record.stage_outputs["evidence"]
    finding = stage["evidence_assessment"]["findings"][0]
    source = stage["evidence"][0]
    if injection == "number":
        finding["claim"] = "测试已经完成，营收增长99%。"
    elif injection == "publisher":
        source["publisher"] = "监管机构"
        finding["claim"] = "监管机构确认测试已经完成。"
    else:
        source["title"] = "NASA test report"
        finding["claim"] = "NASA 的测试已经完成。"
    before = deepcopy(record.model_dump(mode="json"))
    with pytest.raises(ValueError, match="claim boundary"):
        restore_legacy_evidence_stage(record, tmp_path)
    assert record.model_dump(mode="json") == before
    assert not record.evidence_assessment.findings_validated


def saved_later_stages(record):
    record.stage_outputs.update({
        "world": {"world": {"summary": "已保存的条件情景", "actors": [],
            "evidence_refs": ["E001"], "assumptions": [], "variables": {}}},
        "simulation": {"actions": [], "simulation": []},
        "review": {"review": {"status": "passed"}},
    })
    record.status = "interrupted"
    record.stage = "forecast"


def test_explicit_rejection_cannot_resume_past_saved_world_and_review(tmp_path, monkeypatch):
    record = old_record(tmp_path)
    saved_later_stages(record)
    record.stage_outputs["evidence"]["evidence_assessment"]["findings_validated"] = False
    # A stale top-level flag must not override the authoritative saved stage.
    record.evidence_assessment.findings_validated = True
    before = deepcopy(record.stage_outputs)
    constructed = []
    def forbidden_model(**kwargs):
        constructed.append(True)
        raise AssertionError("Rejected evidence must stop before creating a model")
    monkeypatch.setattr(G, "ModelClient", forbidden_model)
    store = RunStore(tmp_path)
    G.execute(record, record.evidence, store, resume=True)
    assert record.status == "failed" and record.failed_stage == "evidence"
    assert "保存的证据发现未经过原文校验" in record.errors[-1]
    assert not constructed
    assert record.stage_outputs == before
    assert store.get(record.run_id).stage_outputs == before
    assert record.forecast is None


def test_missing_legacy_flag_is_validated_before_resuming_saved_later_stages(tmp_path, monkeypatch):
    record = old_record(tmp_path)
    saved_later_stages(record)
    previous_later = deepcopy({stage: record.stage_outputs[stage] for stage in ("world", "simulation", "review")})
    model = ResumeModel()
    monkeypatch.setattr(G, "ModelClient", lambda **kwargs: model)
    G.execute(record, record.evidence, RunStore(tmp_path), resume=True)
    assert record.status == "completed", record.errors
    assert model.roles == ["forecast"]
    assert record.stage_outputs["evidence"]["evidence_assessment"]["findings_validated"] is True
    for stage, output in previous_later.items():
        assert record.stage_outputs[stage] == output
