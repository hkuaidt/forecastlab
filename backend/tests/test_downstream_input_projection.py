"""Regression fixtures use recorded CPU tokenizer results, never generation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from app.model_context import (
    ContextBudgetExceeded, compact_model_payload, downstream_input_projection,
    fit_local_context, model_messages,
)


def fixture(role="review"):
    return json.loads((Path(__file__).parent / "fixtures" / f"{role}_context_run_0602.json").read_text())


def assert_preserved(original, projected):
    compact = compact_model_payload(original)
    framing = compact.get("question_framing")
    if isinstance(framing, dict) and isinstance(framing.get("proposed_spec"), dict):
        if all(k in compact["question"] and compact["question"][k] == v for k, v in framing["proposed_spec"].items()):
            framing.pop("proposed_spec")
    assert projected["evidence_assessment"] == compact["evidence_assessment"]
    for key in ("question", "question_framing", "world", "actions", "simulation", "review"):
        assert projected.get(key) == compact.get(key)
    for key in compact:
        if key.startswith("valid_"):
            assert projected[key] == compact[key]
    findings = compact["evidence_assessment"]["findings"]
    cited = {c["evidence_id"] for f in findings for c in f["citations"]}
    assert len(projected["evidence"]) == len(compact["evidence"])
    for before, after in zip(compact["evidence"], projected["evidence"]):
        assert {k: v for k, v in after.items() if k not in {"passages", "excerpt"}} == {
            k: v for k, v in before.items() if k not in {"passages", "excerpt"}}
        if before["id"] in cited:
            assert "passages" not in after and "excerpt" not in after
        else:
            assert len(after["passages"]) == 1
            assert after["passages"][0] in before["passages"]
    sources = {s["id"]: s for s in compact["evidence"]}
    for finding in findings:
        for citation in finding["citations"]:
            source = sources[citation["evidence_id"]]
            passage = next(p for p in source["passages"] if p["paragraph_id"] == citation["paragraph_id"])
            assert citation["snapshot_hash"] == source["snapshot_hash"] == passage["snapshot_hash"]
            assert citation["quote"] == passage["text"][citation["start"]-passage["start"]:citation["end"]-passage["start"]]


@pytest.mark.parametrize("role", ["review", "forecast"])
def test_actual_failed_run_complete_messages_fit_recorded_cpu_token_budget(role):
    frozen = fixture(role)
    original = deepcopy(frozen["payload"])

    def recorded_counter(messages):
        # Fail on ANY payload/prompt/schema change. A made-up chars/token ratio
        # would not prove the original failing 16k request has been repaired.
        digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        assert digest == frozen["projected_messages_sha256"]
        return frozen["measured_projected_input_tokens"]

    result = fit_local_context(frozen["payload"], **frozen["model"], token_counter=recorded_counter)
    assert frozen["payload"] == original
    assert result.input_tokens + 4096 <= 16384
    assert result.output_tokens == 4096
    assert result.payload == json.loads(result.messages[1]["content"])
    assert not result.omitted_finding_ids
    assert len(result.payload["evidence"]) == 10
    assert len(result.payload["evidence_assessment"]["findings"]) == 7
    assert_preserved(original, result.payload)


def test_all_parent_states_refusals_unresolved_conditions_and_quotes_survive():
    payload = fixture("forecast")["payload"]
    payload["actions"].append({"id": "A999", "actor_id": "ACT1", "parent_ids": ["E001", "F001", "H001", "S001"],
        "parent_state": {"approval": "pending"}, "conditions": ["Only after external validation"],
        "action": "Decline deployment until evidence is available", "refusal_reason": "insufficient evidence"})
    payload["simulation"].append({"id": "S999", "parent_ids": ["A999", "S001"],
        "parent_state": {"approval": "pending"}, "next_state": {"approval": "pending"},
        "state_changes": {}, "summary": "Deployment was refused", "unresolved": ["external validation"],
        "conflicts": ["resources insufficient"], "conditions": ["funding required"]})
    original = deepcopy(payload)
    projected = downstream_input_projection(payload)
    assert payload == original
    assert_preserved(original, projected)
    assert downstream_input_projection(projected) == projected


@pytest.mark.parametrize("damage", ["hash", "quote", "offset", "paragraph", "validation"])
def test_invalid_or_unvalidated_citation_never_replaces_source_text(damage):
    payload = fixture()["payload"]
    citation = payload["evidence_assessment"]["findings"][0]["citations"][0]
    if damage == "hash":
        citation["snapshot_hash"] = "unverified"
    elif damage == "quote":
        citation["quote"] += " fabricated"
    elif damage == "offset":
        citation["start"] = -1
    elif damage == "paragraph":
        citation["paragraph_id"] = "missing"
    else:
        payload["evidence_assessment"]["findings_validated"] = False
    projected = downstream_input_projection(payload)
    assert "evidence_view" not in projected
    assert projected["evidence"] == compact_model_payload(payload)["evidence"]
    assert projected["evidence_assessment"] == compact_model_payload(payload)["evidence_assessment"]


def test_multisource_finding_keeps_every_exact_quote_and_limitation():
    payload = fixture()["payload"]
    findings = payload["evidence_assessment"]["findings"]
    findings[0]["citations"].extend(deepcopy(findings[1]["citations"]))
    findings[0]["limitation"] = "A statement of one source does not establish a global rate."
    projected = downstream_input_projection(payload)
    assert projected["evidence_assessment"]["findings"] == findings
    assert_preserved(payload, projected)


def test_overflow_never_hides_any_source_finding_or_causal_node_to_force_fit():
    payload = fixture("forecast")["payload"]
    original = deepcopy(payload)
    seen = []

    def oversized(messages):
        candidate = json.loads(messages[1]["content"])
        seen.append(candidate)
        assert {e["id"] for e in candidate["evidence"]} == {e["id"] for e in original["evidence"]}
        assert candidate["evidence_assessment"]["findings"] == original["evidence_assessment"]["findings"]
        for key in ("world", "actions", "simulation", "review"):
            assert candidate[key] == original[key]
        return 20000

    with pytest.raises(ContextBudgetExceeded):
        fit_local_context(payload, role="forecast", schema={}, instructions="", token_counter=oversized)
    assert seen and payload == original


def test_feedback_and_full_schema_remain_part_of_counted_messages():
    frozen = fixture()
    feedback = "Keep every causal parent and exact quote."
    messages = model_messages(**frozen["model"], payload=frozen["payload"], repair_feedback=feedback)
    assert feedback in messages[1]["content"]
    assert frozen["model"]["instructions"] in messages[0]["content"]
    assert json.dumps(frozen["model"]["schema"], ensure_ascii=False, separators=(",", ":")) in messages[0]["content"]


def test_environment_protocol_without_sources_remains_intact():
    payload = {"actions": [{"id": "A001", "parent_ids": ["E001", "H001"]}], "simulation": [], "state": {"pending": True}}
    assert downstream_input_projection(payload) == payload
    assert fit_local_context(payload, role="review", schema={}, instructions="", token_counter=lambda _: 100).payload == payload


def test_only_identical_proposed_spec_is_removed_while_user_conditions_stay():
    payload = fixture("forecast")["payload"]
    original = deepcopy(payload)
    assert "proposed_spec" not in downstream_input_projection(payload)["question_framing"]
    assert payload == original
    payload["question_framing"]["proposed_spec"]["question"] += " A distinct user boundary"
    payload["question_framing"]["clarification_answers"] = [{"field": "scope", "answer": "Formal proof only"}]
    assert downstream_input_projection(payload)["question_framing"] == payload["question_framing"]
