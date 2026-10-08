from copy import deepcopy
from datetime import timedelta
from fastapi.testclient import TestClient
import pytest
from app import config
from app.api import create_app
from app.demo import DEMO_QUESTION
from app.schemas import AnalyzeQuestionRequest, ImportedEvidence, QuestionSpec, utcnow
from app.sources import import_evidence
from app.storage import RunStore
from test_question_framing import candidate, request, service
from test_question_evidence_integration import confirmed, WorkflowModel


def test_import_duplicates_keep_aliases_not_independent_votes(tmp_path):
    q = QuestionSpec(question="项目是否能够如期发布？", mode="scenario", as_of=utcnow())
    result = import_evidence([
        ImportedEvidence(title="原始公告", source_url="https://example.org/a", excerpt="同一份资料原文。"),
        ImportedEvidence(title="转述副本", source_url="https://another.example.org/a", excerpt="同一份资料原文。")], q, tmp_path)
    assert len(result.evidence) == 1
    assert len(result.evidence[0].aliases) == 2
    assert result.evidence[0].id == "E001"


def test_revision_operation_retry_returns_same_saved_revision(tmp_path, clear_framing, mock_model):
    model = mock_model([candidate(clear_framing), candidate(clear_framing)])
    svc = service(tmp_path, model)
    first = svc.analyze(request(clear_framing))
    second_request = request(clear_framing, draft_id=first.draft_id, expected_revision=first.revision, operation_id="revision-retry")
    second = svc.analyze(second_request)
    retried = svc.analyze(second_request)
    assert retried == second and model.call_count == 2


def test_demo_parent_cannot_supply_real_run_evidence(tmp_path, clear_framing, monkeypatch):
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-only")
    monkeypatch.setattr("app.graph.ModelClient", lambda **kw: WorkflowModel())
    app = create_app(tmp_path)
    with TestClient(app) as client:
        parent = client.post("/api/runs", json={"question": DEMO_QUESTION.model_dump(mode="json"), "evidence_mode": "demo"}).json()["run_id"]
        c = confirmed(app.state.store, clear_framing)
        response = client.post("/api/runs", json={"confirmation_id": c.confirmation_id, "evidence_mode": "reuse", "parent_run_id": parent})
        assert response.status_code == 422, response.text
        assert "教学" in response.json()["detail"]


def test_draft_read_includes_failed_request_ledger(tmp_path, clear_framing):
    from app.schemas import QuestionFraming
    store = RunStore(tmp_path)
    store.create_draft(QuestionFraming.model_validate(clear_framing))
    c = store.reserve_call("draft_test", "preparation", call_limit=6, input_hash="x", prompt_version="v1")
    c.status = "failed"; c.error_type = "APITimeoutError"; store.finish_call(c)
    view = store.get_draft("draft_test")
    assert hasattr(view, "preparation_records"), "failed preparation usage must be inspectable"
    assert view.preparation_records[0].request_id == c.request_id
    assert view.preparation_records[0].usage_known is False


def test_elapsed_union_does_not_reset_or_double_count_parallel_calls():
    import app.llm as L
    from app.schemas import ModelCallRecord
    assert hasattr(L, "request_active_seconds")
    t = utcnow()
    def row(rid, start, elapsed):
        return ModelCallRecord(request_id=rid, owner_id="run_x", phase="runtime", input_hash="x",
            started_at=t+timedelta(seconds=start), elapsed_seconds=elapsed)
    records = [row("a",0,45), row("b",0,45), row("c",50,45), row("d",100,45)]
    assert L.request_active_seconds(records) == 135
    assert L.request_active_seconds(records + records) == 135


def test_mixed_rejected_target_query_does_not_retain_rejected_framing(tmp_path, clear_framing):
    from app.schemas import QuestionFraming, QuestionPremise, ConfirmQuestionRequest, PremiseDecision
    frame = QuestionFraming.model_validate(clear_framing)
    frame.premises.append(frame.premises[0].model_copy(update={"id": "P002", "content": "另一个前提"}))
    frame.retrieval_plan[0].target_premise_ids = ["P001", "P002"]
    store = RunStore(tmp_path); store.create_draft(frame)
    c = store.confirm_draft(frame.draft_id, ConfirmQuestionRequest(expected_revision=1, decisions=[
        PremiseDecision(premise_id="P001", user_review="rejected"), PremiseDecision(premise_id="P002", user_review="retained")]))
    assert c.framing.retrieval_plan == [], "drop tainted query text, let retrieval use a neutral fallback"


def test_import_evidence_can_mark_server_validated_cutoff(tmp_path):
    from datetime import timedelta
    from app.demo import DEMO_QUESTION
    from app.schemas import ImportedEvidence
    from app.sources import import_evidence

    q = DEMO_QUESTION.model_copy(update={"as_of": DEMO_QUESTION.as_of - timedelta(days=30)})
    item = ImportedEvidence(
        file_id="cutoff-fixture",
        title="冻结评测资料",
        excerpt="这是截点前已经存在的固定资料。",
        source_type="exercise",
    )
    result = import_evidence([item], q, tmp_path, cutoff_verified=True)
    assert result.evidence[0].availability == "verified_before_cutoff"
    assert "cutoff_validation" in result.evidence[0].date_basis
