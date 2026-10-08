"""Precise search and confirmed scope terms survive final source re-selection."""
import pytest

from app.agents.evidence import assess_evidence
from app.provenance import load_snapshot, select_passages, split_passages
from app.schemas import Evidence, QuestionDraft, QuestionFraming, QuestionSpec, RetrievalLog, RetrievalResult
from app.provenance import save_snapshot


@pytest.mark.parametrize("term_source", ["retrieval_log", "retrieval_plan", "resolved_answer", "unresolved_answer"])
def test_specific_body_selected_without_expanding_budget_or_promoting_scope_to_fact(tmp_path, term_source):
    # Only one whole paragraph fits. With the generic Chinese question both English
    # paragraphs tie and the earlier generic introduction wins.
    introduction = "Research collaboration involves organizational choices and shared responsibilities. " * 11
    specific = "Lean proof records let independent reviewers replay the verification steps. " + (
        "Proof objects are checked against explicit declarations and definitions. " * 11)
    body = introduction + "\n" + specific
    snapshot = save_snapshot(body, {"provider": "fixture"}, tmp_path)
    question = QuestionSpec(question="不同工具将如何影响科研协作？", mode="scenario")
    generic = select_passages(split_passages(snapshot), [question.question, question.resolution_rule], limit=1400)
    assert [p.text for p in generic] == [introduction]
    source = Evidence(id="E001", file_id="fixture:selection", title="核验资料", excerpt=body,
                      snapshot_path=snapshot.snapshot_path, snapshot_hash=snapshot.snapshot_hash,
                      retrieved_at=snapshot.stored_at, content_hash=snapshot.snapshot_hash,
                      source_type="exercise", source_group="selection-fixture", date_status="synthetic")
    retrieval = RetrievalResult(evidence=[source])
    frame = None
    if term_source == "retrieval_log":
        retrieval.retrieval_log = [RetrievalLog(task_id="R001", query="Lean proof records",
                                              purpose="background", status="success")]
    else:
        frame = QuestionFraming(
            draft_id="scope-fixture", revision=1, raw_question=question.question,
            proposed_spec=QuestionDraft(question=question.question, mode="scenario", as_of=question.as_of),
            status="ready_for_confirmation",
            retrieval_plan=[{"id":"R001", "query":"Lean proof records", "purpose":"background"}]
                if term_source == "retrieval_plan" else [],
            clarifications=[{"id":"C001", "field":"研究范围", "question":"具体关注什么工具？",
                             "answer":"Lean proof records", "blocking":False,
                             "status":"resolved" if term_source == "resolved_answer" else "open"}]
                if term_source.endswith("answer") else [],
        )

    class Capture:
        def complete(self, role, payload, schema, instructions, **kwargs):
            assert role == "evidence12"
            passages = payload["evidence"][0]["passages"]
            expected = introduction if term_source == "unresolved_answer" else specific
            assert [p["text"] for p in passages] == [expected]
            assert sum(len(p["text"]) for p in passages) <= 1400
            if frame is None:
                assert payload["question_framing"] is None
            else:
                assert payload["question_framing"]["premises"] == []
            return schema.model_validate({"summary":"提供了所选段落。", "findings":[]})

    result = assess_evidence(question, frame, retrieval, Capture(), tmp_path)
    assert result.findings == []
    selected = retrieval.evidence[0]
    stored = load_snapshot(selected, tmp_path)
    assert stored.text == body and selected.snapshot_hash == snapshot.snapshot_hash
    assert all(p.text == stored.text[p.start:p.end] for p in selected.passages)
