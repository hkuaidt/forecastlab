from scenario_fixtures import scenario_output
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from app import config
from app.api import create_app, report_html
from app.graph import build_graph, execute
from app.schemas import (QuestionFraming, QuestionSpec, RunRecord, ConfirmQuestionRequest, PremiseDecision,
                         ImportedEvidence, RetrievalResult, RetrievalLog, utcnow)
from app.sources import import_evidence
from app.storage import RunStore, VersionConflict


class WorkflowModel:
    def __init__(self, **kwargs):
        self.calls = []
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.actual_model = "fixture"
    def complete(self, role, payload, schema, instructions, **kwargs):
        self.calls.append((role, deepcopy(payload)))
        self.usage["calls"] += 1
        if role == "evidence12":
            e = payload["evidence"][0]; p = e["passages"][0]
            body = {"summary": "固定资料的校验结果", "findings": [{"target_premise_ids": ["P001"], "claim": "资料声称测试已完成",
                "relation": "supports", "citations": [{"evidence_id": e["id"], "snapshot_hash": e["snapshot_hash"],
                "paragraph_id": p["paragraph_id"], "quote": "测试已完成"}]}]}
        elif role == "world":
            body = {"summary": "只依据固定资料", "actors": [], "assumptions": [], "evidence_refs": ["E001"]}
        elif role == "review":
            body = {"status": "passed"}
        elif role == "forecast":
            body = {"status": "completed", "conclusion": "这是固定测试结果", "probabilities": {"推进": 0.5, "延后": 0.3, "受限": 0.2},
                    "supporting": [{"text": "资料声称测试已完成", "evidence_ids": ["E001"]}]}
        else:
            raise AssertionError(f"Unexpected role {role}")
        return schema.model_validate(scenario_output(body))


def confirmed(store, data, *, condition=False):
    frame = QuestionFraming.model_validate(data)
    frame.proposed_spec.mode = "scenario"
    frame.proposed_spec.as_of = utcnow()
    store.create_draft(frame)
    return store.confirm_draft(frame.draft_id, ConfirmQuestionRequest(expected_revision=1, decisions=[
        PremiseDecision(premise_id="P001", user_review="retained", treatment="scenario_condition" if condition else "to_verify")]))


def material(q, path):
    return import_evidence([ImportedEvidence(file_id="test", title="测试资料", excerpt="测试已完成，但不能据此确定未来的结果。")], q, path)


def test_confirmed_request_controls_graph_input(tmp_path, clear_framing, monkeypatch):
    model = WorkflowModel()
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-only")
    monkeypatch.setattr("app.graph.ModelClient", lambda **kwargs: model)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        c = confirmed(app.state.store, clear_framing)
        response = client.post("/api/runs", json={"confirmation_id": c.confirmation_id, "evidence_mode": "import",
            "evidence": [{"file_id": "test", "title": "测试资料", "excerpt": "测试已完成，但尚不确定未来。"}]})
        assert response.status_code == 202, response.text
        run = client.get(f"/api/runs/{response.json()['run_id']}").json()
        assert run["question_origin"] == "confirmed" and run["status"] == "completed", run["errors"]
        assert "question" not in [role for role, _ in model.calls]
        payloads = dict(model.calls)
        assert payloads["evidence12"]["question_framing"]["draft_id"] == c.draft_id
        assert payloads["world"]["question_framing"]["revision"] == c.revision
        assert payloads["world"]["evidence_assessment"]["findings"][0]["citations"]
        assert "id" not in payloads["world"]["question_framing"]["premises"][0]
        assert "target_premise_ids" not in payloads["world"]["evidence_assessment"]["findings"][0]
        # The saved audit keeps the original premise identity and its mapping.
        assert run["question_framing"]["premises"][0]["id"] == "P001"
        assert run["evidence_assessment"]["findings"][0]["target_premise_ids"] == ["P001"]
        assert client.get(f"/api/runs/{run['run_id']}/evidence-assessment").status_code == 200
        assert client.get(f"/api/runs/{run['run_id']}/evidence/E001/passages").json()["text"]


def test_legacy_run_is_explicitly_legacy(tmp_path):
    from app.demo import DEMO_QUESTION
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/runs", json={"question": DEMO_QUESTION.model_dump(mode="json"), "evidence_mode": "demo"})
        run = client.get(f"/api/runs/{response.json()['run_id']}").json()
        assert run["question_origin"] == "legacy_direct"
        assert "旧版直接输入" in client.get(f"/api/runs/{run['run_id']}/export").text


@pytest.mark.parametrize("condition", [False, True])
def test_to_verify_is_not_added_as_user_condition(tmp_path, clear_framing, condition):
    store = RunStore(tmp_path); c = confirmed(store, clear_framing, condition=condition)
    record = RunRecord(run_id="test-condition", question=c.question, question_framing=c.framing, confirmation_id=c.confirmation_id,
        question_origin="confirmed", evidence_mode="import", model="fixture")
    model = WorkflowModel(); items = material(c.question, tmp_path).evidence
    output = build_graph(record, items, model, tmp_path).invoke({"question": c.question.model_dump(mode="json")})
    mapping = output.get("premise_assumption_map", {})
    assert ("P001" in mapping) == condition
    assert bool([a for a in output["world"]["assumptions"] if a["created_by"] == "user"]) == condition


def test_reuse_reassesses_without_mutating_parent(tmp_path, clear_framing, monkeypatch):
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-only")
    models = []
    def factory(**kwargs):
        model = WorkflowModel(); models.append(model); return model
    monkeypatch.setattr("app.graph.ModelClient", factory)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        c = confirmed(app.state.store, clear_framing)
        first = client.post("/api/runs", json={"confirmation_id": c.confirmation_id, "evidence_mode": "import",
            "evidence": [{"file_id": "x", "title": "X", "excerpt": "测试已完成"}]}).json()
        before = app.state.store.get(first["run_id"]).model_dump()
        second = client.post("/api/runs", json={"confirmation_id": c.confirmation_id, "evidence_mode": "reuse", "parent_run_id": first["run_id"]})
        assert second.status_code == 202, second.text
        assert any(role == "evidence12" for role, _ in models[-1].calls)
        assert before == app.state.store.get(first["run_id"]).model_dump()


def test_failed_retrieval_logs_survive_resume(tmp_path, clear_framing, monkeypatch):
    store = RunStore(tmp_path); c = confirmed(store, clear_framing)
    record = RunRecord(run_id="failed-retrieval", question=c.question, question_framing=c.framing, confirmation_id=c.confirmation_id,
        question_origin="confirmed", evidence_mode="online", model="fixture")
    monkeypatch.setattr("app.graph.ModelClient", lambda **kw: WorkflowModel())
    import app.graph as G
    assert hasattr(G, "retrieve_evidence")
    count = []
    def fail(*args):
        count.append(1)
        return RetrievalResult(status="failed", retrieval_log=[RetrievalLog(task_id="R001", query="test", status="failed", error="ConnectError")])
    monkeypatch.setattr(G, "retrieve_evidence", fail)
    execute(record, [], store)
    assert record.status == "failed" and record.evidence_assessment.retrieval_log
    assert "evidence" not in record.stage_outputs
    execute(store.get(record.run_id), [], store, resume=True)
    assert count == [1], "resume must not exceed the original three-search budget"
    assert store.get(record.run_id).evidence_assessment.retrieval_log


def test_export_includes_real_origin_and_escapes_html(tmp_path, clear_framing):
    store = RunStore(tmp_path); c = confirmed(store, clear_framing)
    record = RunRecord(run_id="export-test", question=c.question, question_framing=c.framing,
        question_origin="confirmed", evidence_mode="import", model="fixture")
    record.question_framing.raw_question = "<script>alert(1)</script>用户原话"
    html = report_html(record)
    assert "已确认的问题" in html and "&lt;script&gt;" in html
    assert "<script>alert" not in html


def test_stale_confirmation_rejected_at_atomic_run_insert(tmp_path, clear_framing):
    store = RunStore(tmp_path); c = confirmed(store, clear_framing)
    record = RunRecord(run_id="stale", question=c.question, question_framing=c.framing, confirmation_id=c.confirmation_id,
        question_origin="confirmed", evidence_mode="import", model="fixture")
    newer = c.framing.model_copy(update={"revision": 2})
    store.append_revision(newer, expected_revision=1)
    with pytest.raises(VersionConflict):
        store.save(record)
    assert store.get("stale") is None


def test_confirmed_simulation_does_not_require_optional_assessment_payload(tmp_path, clear_framing):
    from app.demo import demo_output
    class ActorModel(WorkflowModel):
        environment_calls = 0
        def complete(self, role, payload, schema, instructions, **kwargs):
            if role in {"world", "actor", "environment"}:
                body = demo_output(role, payload.get("actor", {}).get("id"), payload.get("round", 1))
                if role == "world":
                    body["evidence_refs"] = ["E001"]
                    for actor in body["actors"]:
                        actor["visible_evidence_ids"] = ["E001"]
                    for assumption in body["assumptions"]:
                        assumption["parent_ids"] = ["E001"]
                else:
                    body["evidence_ids"] = ["E001"]
                if role == "environment":
                    self.environment_calls += 1
                    assert "evidence_assessment" not in payload
                    assert payload["question_framing"]["premises"][0]["content"]
                return schema.model_validate(scenario_output(body))
            return super().complete(role, payload, schema, instructions, **kwargs)
    store = RunStore(tmp_path)
    c = confirmed(store, clear_framing)
    record = RunRecord(run_id="run_confirmed_actors", question=c.question, question_framing=c.framing,
        confirmation_id=c.confirmation_id, question_origin="confirmed", evidence_mode="import", model="fixture")
    model = ActorModel()
    output = build_graph(record, material(c.question, tmp_path).evidence, model, tmp_path).invoke({"question": c.question.model_dump(mode="json")})
    assert model.environment_calls == 2
    assert len(output["actions"]) == 6
    assert sum(output["forecast"]["probabilities"].values()) == 1


def test_review_can_audit_an_existing_finding_but_not_invent_one(tmp_path, clear_framing):
    class FindingReviewModel(WorkflowModel):
        def complete(self, role, payload, schema, instructions, **kwargs):
            body = super().complete(role, payload, schema, instructions, **kwargs)
            if role == "evidence12":
                invalid = body.findings[0].model_copy(deep=True)
                invalid.citations[0].quote = "不在来源原文中的引文"
                body.findings.append(invalid)
            if role == "review":
                from app.schemas import ReviewIssue
                assert "F001" in payload["valid_affected_ids"]
                body.issues = [ReviewIssue(severity="medium", claim="发现的范围需要保留", explanation="只审查实际保存的发现。", affected_ids=["F001"])]
            return body
    store = RunStore(tmp_path); c = confirmed(store, clear_framing)
    record = RunRecord(run_id="run_finding_audit", question=c.question, question_framing=c.framing,
        confirmation_id=c.confirmation_id, question_origin="confirmed", evidence_mode="import", model="fixture")
    output = build_graph(record, material(c.question, tmp_path).evidence, FindingReviewModel(), tmp_path).invoke({"question": c.question.model_dump(mode="json")})
    assert output["review"]["issues"][0]["affected_ids"] == ["F001"]
    assert output["evidence_assessment"]["rejected_findings"]
    from app.graph import check_ids
    with pytest.raises(ValueError, match="F999"):
        check_ids(["F999"], {"F001"}, "审查意见")
