"""Normalize bindings only for a genuinely empty premise domain, preserving audit."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from app.agents.evidence import assess_evidence, normalize_empty_premise_targets, validate_findings, _details
from app.schemas import AssessmentCandidate, Evidence, ImportedEvidence, QuestionFraming, QuestionSpec, RetrievalLog
from app.sources import import_evidence


def replay(index=0):
    raw = json.loads((Path(__file__).parent / "fixtures/empty_premise_binding_replays.json").read_text())[index]
    candidate = AssessmentCandidate.model_validate(raw["candidate"])
    frame = QuestionFraming.model_validate(raw["framing"])
    evidence = [Evidence.model_validate(e) for e in raw["evidence"]]
    return raw, candidate, frame, evidence


@pytest.mark.parametrize("index", [0, 1])
def test_real_q_and_field_name_binding_replays_keep_only_quote_checked_findings(index):
    raw, candidate, frame, evidence = replay(index)
    original = candidate.model_dump()
    normalized, audit = normalize_empty_premise_targets(candidate, frame)
    assert normalized is not candidate and candidate.model_dump() == original
    assert all(not item.target_premise_ids for item in [*normalized.findings, *normalized.gaps])
    bindings = json.loads(audit.split("原始绑定=",1)[1])
    assert bindings["findings"] == [{"candidate_index":i,"original_target_premise_ids":item.target_premise_ids}
        for i,item in enumerate(candidate.findings)]
    assert bindings["gaps"][0]["original_target_premise_ids"] == candidate.gaps[0].target_premise_ids
    findings, rejected = validate_findings(normalized, frame, evidence, {e.id:e.passages for e in evidence})
    assert len(findings) == raw["expected_valid_count"] == 5
    assert len(rejected) == raw["expected_rejected_count"] == 1
    assert "前提" not in rejected[0].reason
    _, gaps, invalid = _details(normalized, findings, frame,
        [RetrievalLog.model_validate(log) for log in raw["retrieval_log"]], QuestionSpec.model_validate(raw["question"]))
    assert len(gaps) == 1 and not invalid
    assert candidate.model_dump() == original


@pytest.mark.parametrize("damage", ["evidence_id", "quote", "snapshot_hash"])
def test_empty_domain_never_skips_source_quote_or_hash_validation(damage):
    _, candidate, frame, evidence = replay()
    candidate.findings[0].citations[0] = candidate.findings[0].citations[0].model_copy(update={damage:"INVALID"})
    original = candidate.model_dump()
    normalized, audit = normalize_empty_premise_targets(candidate, frame)
    findings, rejected = validate_findings(normalized, frame, evidence, {e.id:e.passages for e in evidence})
    assert len(findings) == 4 and len(rejected) == 2
    assert rejected[0].candidate["candidate_index"] == 0
    assert candidate.model_dump() == original and audit


@pytest.mark.parametrize("review", ["retained", "rejected"])
def test_any_real_p_even_if_rejected_keeps_strict_binding_validation(clear_framing, review):
    _, candidate, frame, evidence = replay()
    existing = QuestionFraming.model_validate(clear_framing).premises[0]
    existing.user_review = review
    frame.premises = [existing]
    # A rejected actual P cannot be erased by the empty-domain exception.
    if review == "rejected":
        candidate.findings[0].target_premise_ids = [existing.id]
    original = candidate.model_dump()
    normalized, audit = normalize_empty_premise_targets(candidate, frame)
    assert normalized.model_dump() == original and audit is None
    findings, rejected = validate_findings(normalized, frame, evidence, {e.id:e.passages for e in evidence})
    assert not findings and len(rejected) == 6
    assert all("不存在或被用户否认的前提" in r.reason for r in rejected)


def test_empty_domain_normalization_uses_one_call_and_audit_is_not_rejection(tmp_path):
    raw, _, frame, _ = replay()
    question = QuestionSpec.model_validate(raw["question"])
    text = "兼容测试已经完成。"
    retrieval = import_evidence([ImportedEvidence(file_id="empty-domain",title="测试记录",excerpt=text,body=text)],question,tmp_path)
    class Model:
        calls = 0
        candidate = None
        def complete(self, role, payload, schema, instructions, **kwargs):
            self.calls += 1
            assert payload["valid_target_premise_ids"] == []
            e = payload["evidence"][0]; p = e["passages"][0]
            self.candidate = schema.model_validate({"summary":"原模型摘要", "findings":[{
                "target_premise_ids":["question_framing"], "claim":text,"relation":"background",
                "citations":[{"evidence_id":e["id"],"snapshot_hash":e["snapshot_hash"],
                    "paragraph_id":p["paragraph_id"],"quote":p["text"]}]}],
                "gaps":[{"missing":"未取得采用规则。","target_premise_ids":[question.id]}]})
            return self.candidate
    model = Model(); progress = []
    result = assess_evidence(question,frame,retrieval,model,tmp_path,on_progress=progress.append)
    assert model.calls == 1 and len(result.findings) == 1
    assert result.rejected_findings == [] and progress[0].rejected_findings == []
    assert any("空前提域绑定规范化" in item and "question_framing" in item and question.id in item
               for item in result.summary_audit)
    assert "原模型摘要" in result.summary_audit
    assert model.candidate.findings[0].target_premise_ids == ["question_framing"]
    assert model.candidate.gaps[0].target_premise_ids == [question.id]
    assert result.findings[0].target_premise_ids == []
