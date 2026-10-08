from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.demo import demo_evidence
from app.model_context import (ContextBudgetExceeded, LocalTokenCounter,
    compact_model_payload, conservative_token_count, fit_local_context)
from app.schemas import EvidencePassage


def source(number, text):
    item = demo_evidence()[0].model_copy(deep=True)
    item.id = f"E{number:03}"
    item.excerpt = text
    item.snapshot_hash = hashlib.sha256(text.encode()).hexdigest()
    item.passages = [EvidencePassage(paragraph_id="p001", start=0, end=len(text),
        text=text, snapshot_hash=item.snapshot_hash)]
    data = item.model_dump(mode="json")
    data["aliases"] = [{"source_url": "https://example.org/alias", "metadata": {"unused": "x"*5000}}]
    return data


def finding(number, rows):
    return {"id": f"F{number:03}", "claim": "Quoted fact", "relation": "background",
        "citations": [{"evidence_id": row["id"], "snapshot_hash": row["snapshot_hash"],
            "paragraph_id": "p001", "quote": row["passages"][0]["text"][:80],
            "start": 0, "end": 80} for row in rows]}


def chars(messages):
    return sum(len(message["content"]) for message in messages)


def test_compact_sources_keep_one_body_and_preserve_legacy_excerpt():
    row = source(1, "A source paragraph. "*70)
    original = deepcopy(row)
    result = compact_model_payload({"evidence": [row]})["evidence"][0]
    assert "excerpt" not in result and "aliases" not in result
    assert result["passages"] == row["passages"]
    assert result["snapshot_hash"] == row["snapshot_hash"]
    assert row == original
    row["passages"] = []
    assert compact_model_payload({"evidence": [row]})["evidence"][0]["excerpt"] == row["excerpt"]


def test_whole_request_budget_keeps_complete_quotes_and_does_not_mutate_input():
    rows = [source(i+1, (f"Document {i} states its present observations. "*40)[:1400]) for i in range(10)]
    findings = [finding(i+1, [row]) for i, row in enumerate(rows)]
    payload = {"question": {"question": "What may happen?"}, "evidence": rows,
        "world": {"summary": "Causal analysis. "*50},
        "review": {"issues": [{"claim": "Scope matters", "explanation": "Review details. "*35}]},
        "evidence_assessment": {"summary": "Coverage", "findings_validated": True, "findings": findings},
        "valid_finding_ids": [f["id"] for f in findings], "valid_evidence_ids": [e["id"] for e in rows]}
    original = deepcopy(payload)
    result = fit_local_context(payload, schema={"description": "schema detail "*100},
        instructions="instructions "*30, role="world", max_model_len=13000,
        max_output_tokens=4096, token_counter=chars, repair_feedback="Complete JSON is required")
    assert result.input_tokens + 4096 <= 13000
    assert result.input_tokens == chars(result.messages)
    assert result.output_tokens == 4096
    assert payload == original
    assert "Complete JSON is required" in result.messages[1]["content"]
    assert result.omitted_finding_ids
    visible = {e["id"]: e for e in result.payload["evidence"]}
    for item in result.payload["evidence_assessment"]["findings"]:
        for citation in item["citations"]:
            row = visible[citation["evidence_id"]]
            passage = next(p for p in row["passages"] if p["paragraph_id"] == citation["paragraph_id"])
            assert passage["text"][citation["start"]-passage["start"]:citation["end"]-passage["start"]] == citation["quote"]


def test_cross_source_finding_and_conflict_are_omitted_as_a_whole():
    rows = [source(i+1, "Document contents. "*80) for i in range(3)]
    payload = {"evidence": rows, "valid_finding_ids": ["F001", "F002"],
        "evidence_assessment": {"summary": "Coverage", "findings_validated": True,
            "findings": [finding(1, [rows[0]]), finding(2, rows)],
            "conflict_details": [{"issue": "scope", "finding_ids": ["F001", "F002"]}]}}
    # Make every multi-source prompt too large irrespective of passage size.
    def counter(messages):
        data = json.loads(messages[1]["content"])
        return 20000 if len(data["evidence"]) > 1 else chars(messages)
    result = fit_local_context(payload, schema={}, instructions="", role="world",
        max_model_len=16384, max_output_tokens=4096, token_counter=counter)
    assessment = result.payload["evidence_assessment"]
    assert [f["id"] for f in assessment["findings"]] == ["F001"]
    assert result.omitted_finding_ids == ["F002"]
    assert assessment["conflict_details"] == []
    assert result.payload["valid_finding_ids"] == ["F001"]


def test_oversized_required_question_is_rejected_without_cutting_it():
    payload = {"question": {"question": "Important user wording. "*1000}}
    with pytest.raises(ContextBudgetExceeded, match="上下文不足"):
        fit_local_context(payload, schema={}, instructions="", role="question12",
            max_model_len=16384, max_output_tokens=4096, token_counter=chars)
    assert len(payload["question"]["question"]) > 16384


def test_model_window_returned_by_tokenizer_is_respected():
    class Counter:
        max_model_len = 16384
        def __call__(self, messages):
            self.max_model_len = 5000
            return 1000
    with pytest.raises(ContextBudgetExceeded):
        fit_local_context({}, schema={}, instructions="", role="question", token_counter=Counter())


def test_unavailable_local_tokenizer_uses_conservative_bound(monkeypatch):
    attempted = []
    def offline(*args, **kwargs):
        attempted.append(1)
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx.Client, "post", offline)
    counter = LocalTokenCounter("http://127.0.0.1:18048/v1", "qwen3-8b", enable_thinking=True)
    messages = [{"role": "user", "content": "保留完整信息"}]
    assert counter(messages) == conservative_token_count(messages)
    assert counter(messages) >= len(messages[0]["content"].encode("utf-8"))
    counter(messages)
    assert attempted == [1]
    with pytest.raises(ValueError):
        LocalTokenCounter("https://remote.example/v1", "model")


def test_tokenizer_receives_complete_messages_and_thinking_setting(monkeypatch):
    requests = []
    def post(self, url, **kwargs):
        requests.append((url, kwargs["json"]))
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {"count": 321, "max_model_len": 12000})
    monkeypatch.setattr(httpx.Client, "post", post)
    counter = LocalTokenCounter("http://localhost:18048/v1", "qwen3-8b", enable_thinking=True)
    result = fit_local_context({"question": "Test"}, schema={"type": "object"},
        instructions="Exact instructions", role="world", token_counter=counter)
    assert result.input_tokens == 321
    assert counter.max_model_len == 12000
    assert requests[0][0] == "http://localhost:18048/tokenize"
    assert requests[0][1]["messages"] == result.messages
    assert requests[0][1]["chat_template_kwargs"] == {"enable_thinking": True}


def test_transport_rejects_impossible_context_before_spending_model_call(monkeypatch):
    from app import config
    from app.llm import ModelClient
    from app.schemas import QuestionAnalysis
    reservations = []
    requested = []
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-only")
    monkeypatch.setattr(config, "MODEL_BASE_URL", "http://127.0.0.1:18048/v1")
    monkeypatch.setattr(config, "MODEL_NAME", "qwen3-8b")
    monkeypatch.setattr("app.llm.OpenAI", lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: requested.append(kwargs)))))
    client = ModelClient(on_reserve=lambda *args: reservations.append(args))
    class Counter:
        max_model_len = 16384
        def __call__(self, messages):
            return 20000
    client.context_counter = Counter()
    with pytest.raises(ContextBudgetExceeded):
        client.complete("question", {"question": "A mandatory question"}, QuestionAnalysis, "Keep it")
    assert not reservations and not requested
    assert client.usage["calls"] == 0
    assert client.call_records == []


@pytest.mark.parametrize("trace", [
    {"world": {"evidence_refs": ["E002"]}},
    {"world": {"assumptions": [{"id": "H001", "content": "Dependency", "parent_ids": ["F002"]}]}},
    {"actions": [{"id": "M1-A001", "evidence_ids": ["E002"]}]},
    {"simulation": [{"id": "S1", "evidence_ids": ["E002"]}]},
    {"review": {"issues": [{"claim": "Scope", "affected_ids": ["F002"]}]}},
])
def test_compaction_cannot_hide_sources_required_by_causal_trace(trace):
    rows = [source(i+1, "Source material. "*90) for i in range(2)]
    payload = {"evidence": rows, **trace, "evidence_assessment": {
        "summary": "Coverage", "findings_validated": True,
        "findings": [finding(1, [rows[0]]), finding(2, [rows[1]])]}}
    original = deepcopy(payload)
    def counter(messages):
        data = json.loads(messages[1]["content"])
        return 20000 if len(data["evidence"]) > 1 else chars(messages)
    with pytest.raises(ContextBudgetExceeded, match="上下文不足"):
        fit_local_context(payload, schema={}, instructions="", role="review",
                          max_model_len=16384, max_output_tokens=4096, token_counter=counter)
    assert payload == original


def test_whole_finding_omission_cannot_orphan_assumption_parent():
    rows = [source(1, "Source material. "*90), source(2, "Unrelated short background.")]
    required = finding(1, [rows[0]])
    required["citations"][0]["quote"] = rows[0]["passages"][0]["text"][:500]
    required["citations"][0]["end"] = 500
    payload = {"evidence": rows, "world": {"assumptions": [
        {"id": "H001", "content": "Dependency", "parent_ids": ["F001"]}]},
        "evidence_assessment": {"summary": "Coverage", "findings_validated": True,
                                "findings": [required]}}
    def counter(messages):
        data = json.loads(messages[1]["content"])
        return 20000 if data["evidence_assessment"]["findings"] else 1000
    with pytest.raises(ContextBudgetExceeded, match="上下文不足"):
        fit_local_context(payload, schema={}, instructions="", role="forecast",
                          max_model_len=16384, max_output_tokens=4096, token_counter=counter)


def test_source_omission_filters_review_allowlist_without_removing_other_node_types():
    rows = [source(i+1, "Source material. "*90) for i in range(2)]
    payload = {"evidence": rows, "valid_affected_ids": ["E001", "E002", "F001", "F002", "H001", "A001", "S1"],
        "evidence_assessment": {"summary": "Coverage", "findings_validated": True,
            "findings": [finding(1, [rows[0]]), finding(2, [rows[1]])]}}
    def counter(messages):
        data = json.loads(messages[1]["content"])
        return 20000 if len(data["evidence"]) > 1 else chars(messages)
    result = fit_local_context(payload, schema={}, instructions="", role="review",
                               max_model_len=16384, max_output_tokens=4096, token_counter=counter)
    assert result.payload["valid_affected_ids"] == ["E001", "F001", "H001", "A001", "S1"]
    assert json.loads(result.messages[1]["content"])["valid_affected_ids"] == result.payload["valid_affected_ids"]


def test_trace_only_environment_request_does_not_require_new_evidence():
    payload = {"state": {"summary": "Known state"}, "actions": [
        {"id": "M1-A001", "action": "Conditional action", "evidence_ids": ["E001"]}]}
    result = fit_local_context(payload, schema={}, instructions="", role="environment",
                               max_model_len=16384, max_output_tokens=4096, token_counter=chars)
    assert result.payload == payload


def test_optional_source_can_be_omitted_while_required_trace_and_user_conditions_survive():
    rows = [source(i+1, "Source material. "*90) for i in range(2)]
    world = {"summary": "Conditional state", "evidence_refs": ["E001"],
        "assumptions": [{"id": "H001", "content": "A conditional dependency", "parent_ids": ["F001"]}],
        "actors": [{"id": "A001", "resources": ["Limited staff"], "constraints": ["Fixed capacity"],
                    "visible_evidence_ids": ["E001"]}]}
    question = {"question": "What may happen?", "user_assumptions": ["Staff capacity stays unchanged"]}
    payload = {"question": question, "evidence": rows, "world": world,
        "evidence_assessment": {"summary": "Coverage", "findings_validated": True,
            "findings": [finding(1, [rows[0]]), finding(2, [rows[1]])]}}
    def counter(messages):
        data = json.loads(messages[1]["content"])
        return 20000 if len(data["evidence"]) > 1 else chars(messages)
    result = fit_local_context(payload, schema={}, instructions="", role="forecast",
                               max_model_len=16384, max_output_tokens=4096, token_counter=counter)
    assert [e["id"] for e in result.payload["evidence"]] == ["E001"]
    assert result.payload["world"] == world
    assert result.payload["question"] == question
    sent = json.loads(result.messages[1]["content"])
    assert sent["context_limitations"] == result.limitations
    assert result.omitted_finding_ids == ["F002"]
