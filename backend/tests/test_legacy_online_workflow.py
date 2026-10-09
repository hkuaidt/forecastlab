from scenario_fixtures import scenario_output
"""Direct online requests use persisted retrieval and exact-source Agent 2 validation."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, graph as G, sources
from app.api import create_app
from app.provenance import load_snapshot
from app.schemas import QuestionSpec, utcnow


SOURCE_TEXT = "项目的核心测试已经通过。\n后续发布计划仍需评估。"


class DirectOnlineModel:
    def __init__(self, state, **kwargs):
        self.state = state
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.usage.update(kwargs.get("initial_usage") or {})
        self.actual_model = "fixture"

    def complete(self, role, payload, schema, instructions, **kwargs):
        self.state["model_calls"].append((role, deepcopy(payload)))
        self.usage["calls"] += 1
        if role == "question":
            body = {"normalized_question": payload["question"]["question"],
                    "search_queries": ["项目 发布进展", "项目 发布进展"]}
        elif role == "evidence12":
            source = payload["evidence"][0]
            passage = source["passages"][0]
            body = {"summary": "已取得项目状态原文", "findings": [{
                "claim": passage["text"], "relation": "background",
                "citations": [{"evidence_id": source["id"],
                    "snapshot_hash": source["snapshot_hash"],
                    "paragraph_id": passage["paragraph_id"], "quote": passage["text"]}]}]}
        elif role == "world":
            assert payload["evidence_assessment"]["findings_validated"]
            assert payload["valid_finding_ids"] == ["F001"]
            if self.state["fail_world"]:
                self.state["fail_world"] = False
                raise RuntimeError("fixture world interrupted")
            body = {"summary": "根据当前测试进展评估发布情景。",
                    "actors": [], "evidence_refs": ["E001"]}
        elif role == "review":
            body = {"status": "passed"}
        elif role == "forecast":
            body = {"status": "completed", "conclusion": "发布仍取决于后续验证。",
                    "probabilities": {"推进": 0.5, "延后": 0.3, "受限": 0.2}, "supporting": [{
                        "text": "项目的核心测试已经通过。", "evidence_ids": ["E001"]}]}
        else:
            raise AssertionError(f"Unexpected legacy online role: {role}")
        return schema.model_validate(scenario_output(body))


@pytest.fixture
def online_dependencies(monkeypatch):
    state = {"model_calls": [], "search_calls": [], "fail_world": False, "fail_search": False}
    monkeypatch.setattr(config, "MODEL_API_KEY", "fixture-model-key")
    monkeypatch.setattr(config, "BRAVE_SEARCH_API_KEY", "")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "fixture-search-key")
    monkeypatch.setattr(G, "ModelClient", lambda **kwargs: DirectOnlineModel(state, **kwargs))

    def search(query, **kwargs):
        state["search_calls"].append(query)
        if state["fail_search"]:
            raise RuntimeError("fixture provider unavailable")
        return [{"url": "https://example.org/project-status", "title": "项目状态报告",
                 "content": "搜索摘要未提供测试结论。", "raw_content": SOURCE_TEXT,
                 "score": .9, "published_at": (utcnow() - timedelta(hours=1)).isoformat()}]

    monkeypatch.setattr(sources, "_search_one", search)
    return state


def create_direct_run(client):
    question = QuestionSpec(question="项目后续的发布进展会如何演变？", mode="scenario", as_of=utcnow())
    response = client.post("/api/runs", json={
        "question": question.model_dump(mode="json"), "evidence_mode": "online"})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def test_direct_online_input_validates_full_source_and_finishes_report(tmp_path, online_dependencies):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        run_id = create_direct_run(client)
        record = app.state.store.get(run_id)
        assert record.status == "completed", record.errors
        assert record.question_origin == "legacy_direct"
        assert record.question_framing is None and record.confirmation_id is None
        assert record.retrieval_started and record.retrieval_result.status == "completed"
        assert record.evidence_assessment.findings_validated
        assert record.evidence_assessment.quality_profile.validated_finding_count == 1
        source = record.evidence[0]
        assert source.content_kind == "body"
        snapshot = load_snapshot(source, tmp_path)
        assert snapshot.text == SOURCE_TEXT
        citation = record.evidence_assessment.findings[0].citations[0]
        assert snapshot.text[citation.start:citation.end] == citation.quote
        assert citation.quote == "项目的核心测试已经通过。"
        assert record.stage_outputs["evidence"]["evidence_assessment"]["findings_validated"]
        assert online_dependencies["search_calls"] == ["项目 发布进展", "项目 发布进展"]
        assert [role for role, _ in online_dependencies["model_calls"]] == [
            "question", "evidence12", "world", "review", "forecast"]
        evidence_payload = online_dependencies["model_calls"][1][1]
        assert evidence_payload["question_framing"] is None
        assert evidence_payload["evidence"][0]["passages"][0]["text"] == citation.quote
        assert client.get(f"/api/runs/{run_id}/evidence/E001/passages").status_code == 200
        report = client.get(f"/api/runs/{run_id}/export")
        assert report.status_code == 200 and "发布仍取决于后续验证。" in report.text


def test_world_failure_resumes_saved_online_evidence_without_search_or_reassessment(tmp_path, online_dependencies):
    online_dependencies["fail_world"] = True
    app = create_app(tmp_path)
    with TestClient(app) as client:
        run_id = create_direct_run(client)
        failed = app.state.store.get(run_id)
        assert failed.status == "failed" and failed.failed_stage == "world"
        before_evidence = deepcopy(failed.stage_outputs["evidence"])
        assert failed.evidence_assessment.findings_validated
        response = client.post(f"/api/runs/{run_id}/resume")
        assert response.status_code == 202, response.text
        resumed = app.state.store.get(run_id)
        assert resumed.status == "completed", resumed.errors
        assert resumed.resume_count == 1
        assert resumed.stage_outputs["evidence"] == before_evidence
        assert online_dependencies["search_calls"] == ["项目 发布进展", "项目 发布进展"]
        assert [role for role, _ in online_dependencies["model_calls"]] == [
            "question", "evidence12", "world", "world", "review", "forecast"]
        assert resumed.usage["calls"] == 6


@pytest.mark.parametrize("failure", ["provider", "interrupted"])
def test_failed_online_retrieval_cannot_spend_search_budget_again_on_resume(
        tmp_path, online_dependencies, monkeypatch, failure):
    retrieval_attempts = []
    if failure == "provider":
        online_dependencies["fail_search"] = True
    else:
        def interrupt_retrieval(*args):
            retrieval_attempts.append(1)
            raise RuntimeError("fixture retrieval interrupted before result was saved")
        monkeypatch.setattr(G, "retrieve_evidence", interrupt_retrieval)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        run_id = create_direct_run(client)
        failed = app.state.store.get(run_id)
        assert failed.status == "failed" and failed.failed_stage == "evidence"
        assert failed.retrieval_started
        assert "evidence" not in failed.stage_outputs
        first_searches = list(online_dependencies["search_calls"])
        response = client.post(f"/api/runs/{run_id}/resume")
        assert response.status_code == 202, response.text
        resumed = app.state.store.get(run_id)
        assert resumed.status == "failed" and resumed.failed_stage == "evidence"
        assert resumed.retrieval_result.status == "failed"
        assert resumed.evidence_assessment.retrieval_log[0].status == "failed"
        assert online_dependencies["search_calls"] == first_searches
        assert [role for role, _ in online_dependencies["model_calls"]] == ["question"]
        assert resumed.usage["calls"] == 1
        if failure == "interrupted":
            assert retrieval_attempts == [1]
            assert "不重复花费检索额度" in resumed.evidence_assessment.retrieval_log[0].error
