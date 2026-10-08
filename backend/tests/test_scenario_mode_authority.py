from app.agents.question import finalize_framing
from app.schemas import AnalyzeQuestionRequest, FramingCandidate


def test_scenario_request_cannot_acquire_binary_mode_or_settlement_requirements():
    request = AnalyzeQuestionRequest(question={"question": "该研究未来如何发展？", "mode": "scenario"})
    proposed = request.question.model_dump(mode="json")
    proposed.update(mode="binary", resolve_by="2027-12-31T00:00:00Z", resolution_rule="必须发生才算是", resolution_source="https://example.com/")
    candidate = FramingCandidate(proposed_spec=proposed, clarifications=[
        {"field": field, "question": "请补充二元结算条件"}
        for field in ("mode", "resolve_by", "resolution_rule", "resolution_source")])
    frame = finalize_framing(candidate, request, None)
    assert frame.proposed_spec.mode == "scenario"
    assert frame.proposed_spec.resolve_by is None
    assert frame.proposed_spec.resolution_rule == "" and frame.proposed_spec.resolution_source is None
    assert frame.clarifications == [] and frame.status == "ready_for_confirmation"


def test_scenario_keeps_substantive_question_clarification():
    request = AnalyzeQuestionRequest(question={"question": "它在未来会怎么发展？", "mode": "scenario"})
    candidate = FramingCandidate(proposed_spec=request.question, clarifications=[{"field": "question", "question": "它是指哪个研究对象？"}])
    frame = finalize_framing(candidate, request, None)
    assert [item.field for item in frame.clarifications] == ["question"]
