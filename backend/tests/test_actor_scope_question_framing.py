"""The live actor-scope prompt is a task, even when quoted exactly as a premise."""
import json
from pathlib import Path

import pytest
from app.agents.question import finalize_framing
from app.question_service import QuestionService
from app.schemas import AnalyzeQuestionRequest, FramingCandidate
from app.storage import RunStore


@pytest.mark.parametrize("fixture", ["actor_scope_question_framing.json", "combined_scope_question_framing.json"])
def test_actual_actor_scope_and_pure_questions_are_removed_in_one_analysis(tmp_path, fixture):
    raw = json.loads((Path(__file__).parent / "fixtures" / fixture).read_text())
    keys = {"content", "origin", "source_input_id", "original_span", "rationale", "replaces_id"}
    output = FramingCandidate(proposed_spec=raw["proposed_spec"],
        premises=[{k:v for k,v in p.items() if k in keys} for p in raw["premises"]],
        alternative_directions=raw["alternative_directions"],
        retrieval_plan=[{"query":t["query"], "purpose":t["purpose"], "premise_indexes":[i for i, p in enumerate(raw["premises"]) if p["id"] in t["target_premise_ids"]]}
                        for t in raw["retrieval_plan"]])
    calls = []
    class Model:
        def complete(self, role, payload, schema, instructions, **kwargs):
            calls.append(role)
            assert "它们能采取什么行动" in instructions
            assert "Lean theorem prover official documentation" in instructions
            return output.model_copy(deep=True)
    service = QuestionService(RunStore(tmp_path), model_factory=lambda **kwargs: Model())
    frame = service.analyze(AnalyzeQuestionRequest(question=raw["proposed_spec"]))
    assert calls == ["question12"]
    assert frame.premises == [] and frame.status == "ready_for_confirmation"
    assert frame.raw_question == raw["raw_question"]
    assert frame.proposed_spec.question == raw["proposed_spec"]["question"]
    assert len(frame.retrieval_plan) == 3
    assert all(not t.target_premise_ids for t in frame.retrieval_plan)


@pytest.mark.parametrize(("content", "span"), [
    ("研发预算增加20%", "研发预算增加20%，它们能采取什么具体行动？"),
    ("科研团队已经发布验证工具", "科研团队已经发布验证工具，行动如何相互影响？"),
    ("科研团队投入增加20%", "关注科研团队投入增加20%、模型厂商的行动："),
    ("关注科研团队投入增加20%、模型厂商的行动。", "关注科研团队投入增加20%、模型厂商的行动："),
    ("研发预算增加20%，它们能采取什么具体行动？", "研发预算增加20%，它们能采取什么具体行动？"),
    ("研究人员如何合作已经由协议确定", "研究人员如何合作已经由协议确定"),
    ("关注科研团队、模型厂商。", "关注科研团队、模型厂商：预算增加20%，它们能采取什么行动？"),
    ("预算增加20%", "关注科研团队、模型厂商：预算增加20%，它们能采取什么行动？"),
    ("关注科研团队、模型厂商：预算增加20%，它们能采取什么行动？", "关注科研团队、模型厂商：预算增加20%，它们能采取什么行动？"),
])
def test_mixed_real_assertions_survive_task_and_question_filter(content, span):
    request = AnalyzeQuestionRequest(question={
        "question": span + "请推演后续路径。", "mode":"scenario",
        "as_of":"2026-10-09T00:00:00Z"})
    output = FramingCandidate(proposed_spec=request.question, premises=[{
        "content":content, "original_span":span, "source_input_id":"I001", "origin":"user_explicit"}],
        retrieval_plan=[{"query":"原始协议与投入记录", "purpose":"initial", "premise_indexes":[0]}])
    frame = finalize_framing(output, request, None)
    assert [p.content for p in frame.premises] == [content]
    assert frame.retrieval_plan[0].target_premise_ids == ["P001"]


def test_whole_scope_plus_questions_is_a_task_not_a_fact():
    text = "关注科研团队、模型厂商：它们能采取什么行动，哪些条件下产生不同结果？"
    request = AnalyzeQuestionRequest(question={"question":text, "mode":"scenario"})
    output = FramingCandidate(proposed_spec=request.question, premises=[{
        "content":text, "original_span":text, "source_input_id":"I001", "origin":"user_explicit"}])
    assert finalize_framing(output, request, None).premises == []
