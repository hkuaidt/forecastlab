from copy import deepcopy
import importlib.util
import pytest
from fastapi.testclient import TestClient
from app import config
from app.api import create_app
from app.schemas import (AnalyzeQuestionRequest, ConfirmQuestionRequest, PremiseDecision,
                         FramingCandidate, QuestionFraming)
from app.storage import RunStore


def candidate(data):
    return {"proposed_spec": deepcopy(data["proposed_spec"]),
            "premises": [{k: v for k, v in p.items() if k != "id"} for p in data["premises"]],
            "retrieval_plan": [{"query": "青岚测试进展", "purpose": "initial", "premise_indexes": [0]}]}


def request(data, **extra):
    q = {**data["proposed_spec"], "question": data["raw_question"]}
    return AnalyzeQuestionRequest(question=q, **extra)


def service(tmp_path, model):
    assert importlib.util.find_spec("app.question_service"), "question service not implemented"
    from app.question_service import QuestionService
    return QuestionService(RunStore(tmp_path), model_factory=lambda **kw: model)


def confirm(frame, review="retained"):
    return ConfirmQuestionRequest(expected_revision=frame.revision, decisions=[
        PremiseDecision(premise_id=p.id, user_review=review) for p in frame.premises])


def test_raw_question_reaches_model_without_resolution_rule(tmp_path, clear_framing, mock_model):
    c = candidate(clear_framing); c["proposed_spec"]["resolve_by"] = None; c["proposed_spec"]["resolution_rule"] = ""
    model = mock_model([c]); svc = service(tmp_path, model)
    req = AnalyzeQuestionRequest(question={"question": clear_framing["raw_question"], "as_of": "2026-09-30T08:00:00Z"})
    frame = svc.analyze(req)
    assert model.calls[0][1]["inputs"][0]["text"] == clear_framing["raw_question"]
    assert frame.status == "needs_clarification"


def test_ambiguous_question_blocks_confirmation(tmp_path, clear_framing, mock_model):
    c = candidate(clear_framing); c["clarifications"] = [{"field": "scope", "question": "正式发布包括测试版吗？", "blocking": True}]
    svc = service(tmp_path, mock_model([c])); frame = svc.analyze(request(clear_framing))
    assert frame.status == "needs_clarification"
    with pytest.raises(ValueError):
        svc.confirm(frame.draft_id, confirm(frame))


def test_clear_question_needs_one_analysis_and_retry_is_idempotent(tmp_path, clear_framing, mock_model):
    model = mock_model([candidate(clear_framing)]); svc = service(tmp_path, model)
    req = request(clear_framing, operation_id="same-click")
    frame = svc.analyze(req); duplicate = svc.analyze(req)
    assert frame.status == "ready_for_confirmation"
    a = svc.confirm(frame.draft_id, confirm(frame)); b = svc.confirm(frame.draft_id, confirm(frame))
    assert a.confirmation_id == b.confirmation_id
    assert duplicate == frame and model.call_count == 1


def test_neutral_variant_may_have_no_premises(tmp_path, clear_framing, mock_model):
    c = candidate(clear_framing); c["premises"] = []; c["retrieval_plan"] = []
    svc = service(tmp_path, mock_model([c])); frame = svc.analyze(request(clear_framing))
    assert frame.premises == [] and frame.status == "ready_for_confirmation"


def test_rejection_removes_active_targets(tmp_path, clear_framing, mock_model):
    svc = service(tmp_path, mock_model([candidate(clear_framing)]))
    frame = svc.analyze(request(clear_framing)); result = svc.confirm(frame.draft_id, confirm(frame, "rejected"))
    assert result.framing.premises[0].user_review == "rejected"
    assert all("P001" not in t.target_premise_ids for t in result.framing.retrieval_plan)


def test_changed_premise_gets_new_id(tmp_path, clear_framing, mock_model):
    c = candidate(clear_framing); changed = deepcopy(c)
    changed["premises"][0]["content"] = "用户认为所有测试均已完成"
    model = mock_model([c, changed]); svc = service(tmp_path, model)
    old = svc.analyze(request(clear_framing))
    new = svc.analyze(request(clear_framing, draft_id=old.draft_id, expected_revision=old.revision))
    assert new.raw_question == old.raw_question
    assert new.premises[0].id != old.premises[0].id
    assert new.premises[0].replaces_id == old.premises[0].id


def test_unquoted_premise_is_rejected(tmp_path, clear_framing, mock_model):
    c = candidate(clear_framing); c["premises"][0]["original_span"] = "这里没有这段原话"
    model = mock_model([c, c]); svc = service(tmp_path, model)
    with pytest.raises(ValueError, match="原话"):
        svc.analyze(request(clear_framing))
    assert model.call_count == 2


def test_explicit_date_not_silently_changed(tmp_path, clear_framing, mock_model):
    c = candidate(clear_framing); c["proposed_spec"]["resolve_by"] = "2027-01-01T00:00:00Z"
    svc = service(tmp_path, mock_model([c])); req = request(clear_framing); frame = svc.analyze(req)
    assert frame.proposed_spec.resolve_by == req.question.resolve_by
    assert any(c.field == "resolve_by" for c in frame.clarifications)


def test_api_missing_key_and_unknown_draft_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_API_KEY", "")
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/questions/analyze", json={"question": {"question": "青岚社区能否按期发布正式版本？"}})
        assert response.status_code == 503
        assert client.get("/api/questions/not-a-draft").status_code == 404


def test_api_confirm_does_not_accept_question_override(tmp_path, clear_framing, mock_model):
    model = mock_model([candidate(clear_framing)])
    try:
        app = create_app(tmp_path, question_model_factory=lambda **kw: model)
    except TypeError:
        pytest.fail("create_app lacks model injection for the question service")
    with TestClient(app) as client:
        frame = client.post("/api/questions/analyze", json=request(clear_framing).model_dump(mode="json")).json()
        payload = {"expected_revision": frame["revision"], "decisions": [], "question": "偷偷替换"}
        assert client.post(f"/api/questions/{frame['draft_id']}/confirm", json=payload).status_code == 422


def test_scope_restatements_are_not_promoted_to_premises(tmp_path, clear_framing, mock_model):
    req = AnalyzeQuestionRequest(question={
        "question": "青岚社区是否会在2026年11月15日前发布正式版？",
        "as_of": "2026-09-30T08:00:00Z",
        "resolve_by": "2026-11-15T23:59:00Z",
        "resolution_rule": "官方版本页可下载正式版为是，否则为否。",
        "mode": "binary",
    })
    output = {
        "proposed_spec": req.question.model_dump(mode="json"),
        "premises": [
            {"content": "研究对象是青岚社区正式版发布。", "origin": "user_explicit",
             "source_input_id": "I001", "original_span": "青岚社区是否会在2026年11月15日前发布正式版", "rationale": "研究对象"},
            {"content": "判定标准为官方版本页可下载正式版。", "origin": "user_explicit",
             "source_input_id": "I001", "original_span": "是否会在2026年11月15日前发布正式版", "rationale": "判定口径"},
        ],
        "retrieval_plan": [
            {"query": "青岚正式版", "purpose": "initial", "premise_indexes": [0]},
            {"query": "青岚版本页", "purpose": "background", "premise_indexes": [1]},
        ],
    }
    frame = service(tmp_path, mock_model([output])).analyze(req)
    assert frame.premises == []
    assert frame.retrieval_plan == []


def test_filtered_candidate_indices_still_map_leading_premise_correctly(tmp_path, mock_model):
    req = AnalyzeQuestionRequest(question={
        "question": "既然核心测试已通过，青岚社区是否会在2026年11月15日前发布正式版？",
        "as_of": "2026-09-30T08:00:00Z",
        "resolve_by": "2026-11-15T23:59:00Z",
        "resolution_rule": "官方版本页可下载正式版为是，否则为否。",
        "mode": "binary",
    })
    output = {
        "proposed_spec": req.question.model_dump(mode="json"),
        "premises": [
            {"content": "研究对象是青岚社区正式版发布。", "origin": "user_explicit",
             "source_input_id": "I001", "original_span": "青岚社区是否会在2026年11月15日前发布正式版", "rationale": "研究对象"},
            {"content": "核心测试已通过。", "origin": "user_explicit",
             "source_input_id": "I001", "original_span": "核心测试已通过", "rationale": "用户明确的背景事实"},
        ],
        "retrieval_plan": [
            {"query": "青岚对象", "purpose": "initial", "premise_indexes": [0]},
            {"query": "青岚核心测试", "purpose": "challenge", "premise_indexes": [1]},
        ],
    }
    frame = service(tmp_path, mock_model([output])).analyze(req)
    assert [p.content for p in frame.premises] == ["核心测试已通过。"]
    assert frame.premises[0].id == "P001"
    assert len(frame.retrieval_plan) == 1
    assert frame.retrieval_plan[0].query == "青岚核心测试"
    assert frame.retrieval_plan[0].target_premise_ids == ["P001"]


def test_model_inferred_binary_mode_restatement_is_removed(tmp_path, mock_model):
    req = AnalyzeQuestionRequest(question={
        "question": "青岚社区是否会在2026年11月15日前发布正式版？",
        "as_of": "2026-09-30T08:00:00Z",
        "resolve_by": "2026-11-15T23:59:00Z",
        "resolution_rule": "官方版本页可下载正式版为是，否则为否。",
        "mode": "binary",
    })
    output = {
        "proposed_spec": req.question.model_dump(mode="json"),
        "premises": [{"content": "该问题为二元判定（是/否）。", "origin": "model_inferred",
                      "source_input_id": "I001", "original_span": "是否会", "rationale": "从是否推断"}],
        "retrieval_plan": [],
    }
    frame = service(tmp_path, mock_model([output])).analyze(req)
    assert frame.premises == []
