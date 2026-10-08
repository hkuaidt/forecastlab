from copy import deepcopy
import hashlib
import json
from pydantic import HttpUrl

from app.demo import demo_evidence
from app.model_context import evidence_input_projection, fit_local_context, model_messages
from app.schemas import Evidence, EvidencePassage


def frozen_payload():
    sources = []
    for index in range(10):
        source = demo_evidence()[0].model_copy(deep=True)
        source.id = f"E{index+1:03}"
        source.source_url = HttpUrl(f"https://example.org/source-{index}")
        source.publisher = "Public research archive"
        source.source_group = f"group-{index // 2}"
        source.source_group_basis = "Explicit common publisher"
        source.possible_same_source = [f"https://example.org/source-{index ^ 1}"]
        source.content_kind = "body" if index < 4 else "snippet"
        source.source_type = "secondary" if index < 4 else "snippet_only"
        texts = [(f"Source {index} paragraph {p}. " + "Original observations. " * 40)[:700] for p in range(2)]
        source.excerpt = "\n".join(texts)
        source.snapshot_hash = hashlib.sha256(source.excerpt.encode()).hexdigest()
        source.passages = [EvidencePassage(paragraph_id=f"B{p+1:06}", text=text,
            start=p * 701, end=p * 701 + len(text), snapshot_hash=source.snapshot_hash) for p, text in enumerate(texts)]
        row = source.model_dump(mode="json")
        row["aliases"] = [{"source_url": row["source_url"], "metadata": {"body_fetch": {"status": "success"}}}]
        sources.append(row)
    question = {"question": "How will this research evolve?", "as_of": "2026-10-08T10:00:00Z", "mode": "scenario"}
    return {"question": question, "evidence": sources, "question_framing": {
        "schema_version": 1, "draft_id": "draft_example", "revision": 2, "status": "confirmed",
        "proposed_spec": dict(question), "next_premise_number": 2, "demo_case_id": None,
        "premises": [{"id": "P001", "content": "A hypothesis to check", "user_review": "accepted"}],
        "alternative_directions": ["Look for contrary outcomes"],
        "clarification_answers": [{"field": "scope", "answer": "Formal proof verification"}],
        "retrieval_plan": [{"id": "R001", "query": "formal proof", "purpose": "background", "target_premise_ids": []}]},
        "retrieval_log": [{"task_id": "R001", "query": "formal proof", "purpose": "background",
                           "status": "success", "result_count": 8, "elapsed_seconds": 2.75, "error": None}]}


def test_ten_frozen_sources_preserve_identity_dates_labels_hash_and_all_text():
    payload = frozen_payload()
    original = deepcopy(payload)
    wire = evidence_input_projection(payload)
    assert payload == original
    assert len(wire["evidence"]) == 10
    for before, after in zip(payload["evidence"], wire["evidence"]):
        for field in ("id", "source_url", "publisher", "retrieved_at", "source_type", "source_kind",
                      "source_group", "source_group_basis", "possible_same_source", "content_kind",
                      "availability", "snapshot_hash", "content_truncated"):
            assert after[field] == before[field]
        for field in ("published_at", "updated_at", "event_at"):
            assert after.get(field) == before.get(field)
        assert "aliases" not in after and "excerpt" not in after
        for old, new in zip(before["passages"], after["passages"]):
            assert new == {k: old[k] for k in ("paragraph_id", "text", "snapshot_hash")}
        assert sum(len(p["text"]) for p in after["passages"]) == 1400
    assert wire["question"] == payload["question"]
    assert wire["question_framing"]["premises"] == payload["question_framing"]["premises"]
    assert wire["question_framing"]["clarification_answers"] == payload["question_framing"]["clarification_answers"]
    assert "proposed_spec" not in wire["question_framing"]
    assert "retrieval_plan" not in wire["question_framing"]
    assert "elapsed_seconds" not in wire["retrieval_log"][0]


def test_distinct_spec_unexecuted_plans_and_premise_targets_are_retained():
    payload = frozen_payload()
    payload["question_framing"]["proposed_spec"]["question"] = "A distinct unresolved question"
    payload["question_framing"]["retrieval_plan"][0]["target_premise_ids"] = ["P001"]
    payload["question_framing"]["retrieval_plan"].append({"id": "R002", "query": "unexecuted", "purpose": "alternative", "target_premise_ids": []})
    wire = evidence_input_projection(payload)
    assert wire["question_framing"]["proposed_spec"] == payload["question_framing"]["proposed_spec"]
    assert wire["question_framing"]["retrieval_plan"] == payload["question_framing"]["retrieval_plan"]


def test_wire_projection_keeps_fitting_offsets_and_whole_passages_intact():
    payload = frozen_payload()
    original = deepcopy(payload)
    def counter(messages):
        data = json.loads(messages[1]["content"])
        for source in data["evidence"]:
            assert all("start" not in p and "end" not in p for p in source["passages"])
        return 20000 if any(sum(len(p["text"]) for p in s["passages"]) > 1200 for s in data["evidence"]) else 1000
    result = fit_local_context(payload, schema={}, instructions="Keep citations", role="evidence12", token_counter=counter)
    assert payload == original and result.input_tokens == 1000
    for source in result.payload["evidence"]:
        original_source = next(e for e in original["evidence"] if e["id"] == source["id"])
        parsed = Evidence.model_validate(original_source)
        assert len(source["passages"]) == 1
        assert source["passages"][0] == {key: parsed.passages[0].model_dump()[key] for key in ("paragraph_id", "text", "snapshot_hash")}
    assert result.payload == json.loads(result.messages[1]["content"])
    assert "上下文预算" in result.messages[1]["content"]


def test_only_evidence12_wire_changes_and_feedback_remains_visible():
    payload = frozen_payload()
    for role in ("world", "actor"):
        assert json.loads(model_messages(role, payload, {}, "instructions")[1]["content"]) == payload
    wire = model_messages("evidence12", payload, {}, "instructions", repair_feedback="Missing exact quote")
    assert "Missing exact quote" in wire[1]["content"]
    assert json.loads(wire[1]["content"].split("\n上次")[0]) == evidence_input_projection(payload)
    assert "instructions" in wire[0]["content"] and "JSON Schema:" in wire[0]["content"]
