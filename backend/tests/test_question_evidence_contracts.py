import pytest
from pydantic import ValidationError
import app.schemas as S
from app.demo import DEMO_QUESTION


def test_legacy_record_loads_without_question_evidence_fields(legacy_run_data):
    record = S.RunRecord.model_validate(legacy_run_data)
    assert hasattr(record, "question_framing"), "missing backwards-compatible framing fields"
    assert record.question_framing is None
    assert record.question_origin == "legacy_direct"
    assert record.model_calls == [] and record.active_seconds == 0


def test_run_request_requires_exactly_one_question_source():
    with pytest.raises(ValidationError):
        S.RunRequest(question=DEMO_QUESTION, confirmation_id="confirm_x")
    assert S.RunRequest(confirmation_id="confirm_x").question is None
    with pytest.raises(ValidationError):
        S.RunRequest()
    with pytest.raises(ValidationError):
        S.RunRequest(confirmation_id="confirm_x", question_origin="confirmed")


def test_limits_and_server_owned_ids(clear_framing):
    assert hasattr(S, "QuestionFraming"), "missing framing schema"
    assert S.QuestionFraming.model_validate(clear_framing).schema_version == 1
    with pytest.raises(ValidationError):
        S.QuestionFraming.model_validate({**clear_framing, "schema_version": 2})
    with pytest.raises(ValidationError):
        S.AnalyzeQuestionRequest(question={"question": "测试这个项目是否能够发布？"}, status="ready_for_confirmation")
    with pytest.raises(ValidationError):
        S.FramingCandidate(proposed_spec=clear_framing["proposed_spec"], draft_id="forged")
    with pytest.raises(ValidationError):
        S.QuestionFraming.model_validate({**clear_framing, "retrieval_plan": clear_framing["retrieval_plan"]*4})


def test_confirmation_decisions_cannot_edit_question():
    assert hasattr(S, "ConfirmQuestionRequest")
    with pytest.raises(ValidationError):
        S.ConfirmQuestionRequest(expected_revision=1, decisions=[], question="replaced")
    with pytest.raises(ValidationError):
        S.PremiseDecision(premise_id="P001", user_review="pending")


def test_new_evidence_fields_default_without_inventing_provenance():
    from app.demo import demo_evidence
    evidence = S.Evidence.model_validate(demo_evidence()[0].model_dump())
    assert hasattr(evidence, "snapshot_hash")
    assert evidence.snapshot_hash is None
    assert evidence.source_kind == "unknown"
    assert evidence.availability == "unverified"
