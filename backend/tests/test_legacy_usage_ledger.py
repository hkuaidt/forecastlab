"""Legacy direct/import callers share the durable runtime ledger."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from app import config, graph, llm
from app.api import create_app
from app.schemas import QuestionSpec, RunRecord
from app.storage import RunStore


def legacy_record():
    question = QuestionSpec(question="该研究未来会怎样发展？", mode="scenario")
    return RunRecord(run_id="legacy_ledger", question=question, evidence_mode="import", model="fixture",
        status="interrupted", stage="forecast", stage_outputs={
            "question": {"question_analysis": {"normalized_question": question.question, "search_queries": []}},
            "evidence": {"evidence": [], "evidence_assessment": {"summary": "没有证据", "findings": []}},
            "world": {"world": {"summary": "条件状态", "actors": []}},
            "simulation": {"actions": [], "simulation": []}, "review": {"review": {"status": "passed"}}})


def test_legacy_direct_transport_is_reserved_before_request_and_accounted_afterwards(tmp_path, monkeypatch):
    record = legacy_record(); store = RunStore(tmp_path)
    seen = []
    def create(**kwargs):
        calls = store.list_calls(record.run_id)
        assert len(calls) == 1 and calls[0].status == "reserved"
        seen.append(calls[0].request_id)
        return SimpleNamespace(model="fixture", usage=SimpleNamespace(prompt_tokens=101, completion_tokens=21),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({
                "status":"scenario_only","conclusion":"没有足够证据，仅可列出待验证条件。"})))])
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-only")
    monkeypatch.setattr(config, "MODEL_BASE_URL", "https://example.invalid")
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    graph.execute(record, [], store, resume=True)
    assert record.status == "scenario_only", record.errors
    assert record.usage == {"calls":1,"prompt_tokens":101,"completion_tokens":21}
    assert [c.request_id for c in record.model_calls] == seen
    assert record.model_calls[0].status == "succeeded"
    assert record.active_seconds > 0


def test_legacy_resume_restores_ledger_even_when_stage_usage_is_stale(tmp_path, monkeypatch):
    record = legacy_record(); store = RunStore(tmp_path)
    call = store.reserve_call(record.run_id,"runtime",call_limit=18,input_hash="prior",prompt_version="test")
    call.status="succeeded";call.prompt_tokens=101;call.completion_tokens=21;call.elapsed_seconds=15
    store.finish_call(call)
    captured = {}
    class Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs);self.usage=kwargs["initial_usage"].copy();self.active_seconds=kwargs["initial_active_seconds"]
        def complete(self, role, payload, schema, instructions):
            assert self.usage == {"calls":1,"prompt_tokens":101,"completion_tokens":21}
            self.usage["calls"] += 1
            return schema.model_validate({"status":"scenario_only","conclusion":"条件推演。"})
    monkeypatch.setattr(graph,"ModelClient",Fake)
    graph.execute(record,[],store,resume=True)
    assert record.status=="scenario_only",record.errors
    assert record.usage["calls"]==2 and captured["initial_active_seconds"]==15
    assert callable(captured["on_reserve"]) and callable(captured["on_finish"])


def test_api_exposes_pending_call_before_stage_snapshot_updates(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app) as client:
        record=legacy_record();record.status="running";record.stage="forecast"
        app.state.store.save(record)
        call=app.state.store.reserve_call(record.run_id,"runtime",call_limit=18,input_hash="pending",prompt_version="test")
        body=client.get(f"/api/runs/{record.run_id}").json()
        assert body["model_calls"][0]["request_id"]==call.request_id
        assert body["model_calls"][0]["status"]=="reserved" and body["usage"]["calls"]==1
        assert client.get("/api/runs").json()[0]["usage"]["calls"]==1
        # Reading telemetry does not rewrite the old stage snapshot or usage.
        assert app.state.store.get(record.run_id).usage["calls"]==0
