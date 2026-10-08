from types import SimpleNamespace
import pytest
import httpx
from openai import APIConnectionError
from app import config
import app.llm as L
from app.schemas import ModelCallRecord, QuestionAnalysis
from app.storage import RunStore


def records(prefix, count):
    return [ModelCallRecord(request_id=f"{prefix}{i}", owner_id=prefix, phase="runtime", input_hash="x") for i in range(count)]


def install_transport(monkeypatch, callback=None):
    def create(**kwargs):
        if callback:
            callback()
        return SimpleNamespace(model="fixture", usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"normalized_question":"测试问题","search_queries":["查询"]}'))])
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-only")
    monkeypatch.setattr(L, "OpenAI", lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(L.time, "sleep", lambda _: None)


def ask(client, **kwargs):
    return client.complete("question", {"x": 1}, QuestionAnalysis, "测试", **kwargs)


def test_preparation_reduces_runtime_allowance(monkeypatch):
    assert hasattr(L, "remaining_run_budget")
    assert L.remaining_run_budget(records("prep", 6), [], max_calls=18) == 12
    assert L.remaining_run_budget(records("prep", 6), records("run", 12), max_calls=18) == 0
    install_transport(monkeypatch)
    client = L.ModelClient(call_limit=12)
    for _ in range(12):
        ask(client)
    with pytest.raises(L.BudgetExceeded):
        ask(client)
    assert client.usage["calls"] == 12


def test_preparation_calls_share_six_request_cap(monkeypatch, tmp_path):
    store = RunStore(tmp_path)
    install_transport(monkeypatch)
    for _ in range(6):
        client = L.ModelClient(call_limit=6, on_reserve=lambda h, v: store.reserve_call(
            "draft_x", "preparation", call_limit=6, input_hash=h, prompt_version=v), on_finish=store.finish_call)
        ask(client)
    with pytest.raises(L.BudgetExceeded):
        ask(L.ModelClient(on_reserve=lambda h, v: store.reserve_call("draft_x", "preparation", call_limit=6,
            input_hash=h, prompt_version=v), on_finish=store.finish_call))
    assert len(store.list_calls("draft_x")) == 6
    assert all(c.usage_known for c in store.list_calls("draft_x"))


def test_resume_preserves_usage_and_active_time(monkeypatch):
    install_transport(monkeypatch)
    prior = {"calls": 5, "prompt_tokens": 7, "completion_tokens": 9}
    client = L.ModelClient(initial_usage=prior, initial_active_seconds=299, call_limit=6)
    ask(client)
    assert client.usage == {"calls": 6, "prompt_tokens": 9, "completion_tokens": 10}
    assert prior["calls"] == 5
    with pytest.raises(L.BudgetExceeded):
        ask(client)
    expired = L.ModelClient(initial_active_seconds=300)
    with pytest.raises(L.BudgetExceeded):
        ask(expired)
    assert expired.usage["calls"] == 0


def test_failed_attempt_keeps_unknown_usage(monkeypatch, tmp_path):
    store = RunStore(tmp_path)
    def offline():
        assert store.list_calls("run_x")[0].status == "reserved"
        raise APIConnectionError(request=httpx.Request("POST", "https://example.org/chat"))
    install_transport(monkeypatch, offline)
    client = L.ModelClient(on_reserve=lambda h, v: store.reserve_call("run_x", "runtime", call_limit=18,
        input_hash=h, prompt_version=v), on_finish=store.finish_call)
    with pytest.raises(RuntimeError):
        ask(client, attempt_limit=1)
    row = store.list_calls("run_x")[0]
    assert row.status == "failed" and row.usage_known is False
    assert client.usage["calls"] == 1
    assert "test-only" not in row.model_dump_json()


def test_shared_confirmation_is_not_double_billed():
    assert hasattr(L, "unique_request_count")
    prep = records("p", 6)
    assert L.unique_request_count(prep + prep) == 6
    assert L.remaining_run_budget(prep + prep, records("r", 3), max_calls=18) == 9
