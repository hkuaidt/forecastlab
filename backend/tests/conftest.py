"""Deterministic 问题定义与证据评估 fixtures; never contact a model/search service."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from app.demo import DEMO_QUESTION

@pytest.fixture
def clear_framing():
    return {
        "draft_id": "draft_test", "revision": 1, "raw_question": "青岚社区说测试已完成，能按期发布吗？",
        "proposed_spec": {"question": "青岚社区能否在2026年11月15日前发布正式版？",
                          "as_of": "2026-09-30T08:00:00Z", "resolve_by": "2026-11-15T23:59:00Z",
                          "resolution_rule": "官方版本页可下载正式版为是，否则为否。"},
        "inputs": [{"input_id": "I001", "kind": "original", "text": "青岚社区说测试已完成，能按期发布吗？"}],
        "premises": [{"id": "P001", "content": "测试已完成", "origin": "user_explicit",
                      "source_input_id": "I001", "original_span": "测试已完成", "rationale": "核查测试范围"}],
        "retrieval_plan": [{"id": "R001", "query": "青岚测试进展", "purpose": "initial",
                             "target_premise_ids": ["P001"]}],
        "status": "ready_for_confirmation", "next_premise_number": 2,
    }

@pytest.fixture
def legacy_run_data():
    return {"run_id": "legacy_fixture", "question": DEMO_QUESTION.model_dump(mode="json"),
            "evidence_mode": "demo", "demo": True, "model": "fixture"}

class FixtureModel:
    """Only a test double. Candidate bodies are set by each test."""
    def __init__(self, outputs=()):
        self.outputs = list(outputs)
        self.calls = []
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.actual_model = "fixture"
    @property
    def call_count(self):
        return len(self.calls)
    def complete(self, role, payload, schema, instructions, **kwargs):
        self.calls.append((role, deepcopy(payload), kwargs))
        self.usage["calls"] += 1
        if not self.outputs:
            raise AssertionError("No fixture response was supplied")
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return schema.model_validate(deepcopy(output))

@pytest.fixture
def mock_model():
    return FixtureModel


@pytest.fixture(autouse=True)
def forbid_external_http(monkeypatch):
    """Unit/integration tests must never escape to a real provider with dummy keys."""
    import httpx
    def blocked(*args, **kwargs):
        raise RuntimeError("External HTTP disabled in automated tests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
