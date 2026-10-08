"""Regressions for preserving factual user premises through Agent 1 filtering."""
import pytest

from app.agents.question import finalize_framing
from app.schemas import AnalyzeQuestionRequest, FramingCandidate


def framing_for(claim, *, content=None, origin="user_explicit", resolution_source=None, lead="既然"):
    request = AnalyzeQuestionRequest(question={
        "question": f"{lead}{claim}，该公司下一季度能否完成审计？",
        "mode": "scenario",
        "as_of": "2026-09-30T08:00:00Z",
        "resolution_source": resolution_source,
    })
    candidate = FramingCandidate(
        proposed_spec=request.question,
        premises=[{
            "content": content or claim, "origin": origin,
            "source_input_id": "I001", "original_span": claim,
        }],
        retrieval_plan=[{
            "query": "核查监管原始文件", "purpose": "challenge", "premise_indexes": [0],
        }],
    )
    return finalize_framing(candidate, request, None)


@pytest.mark.parametrize("claim", [
    "数据来源已被监管机构判定造假",
    "研究对象已完成全部临床试验",
    "判定标准已被法院认定无效",
    "结算来源停止发布新数据",
    "比较基准存在重大计算错误",
])
def test_factual_scope_subjects_keep_premises_and_retrieval(claim):
    frame = framing_for(claim)
    assert [p.content for p in frame.premises] == [claim]
    assert frame.premises[0].user_review == "pending"
    assert frame.premises[0].treatment == "to_verify"
    assert frame.retrieval_plan[0].target_premise_ids == ["P001"]


@pytest.mark.parametrize("claim", [
    "数据来源是伪造的",
    "研究对象是一家已经破产的公司",
    "判定标准包含未经检验的假设",
])
def test_verbatim_explicit_assertions_are_not_assumed_to_be_field_assignments(claim):
    frame = framing_for(claim, content=f"{claim}。")
    assert [p.content for p in frame.premises] == [f"{claim}。"]
    assert frame.retrieval_plan[0].target_premise_ids == ["P001"]


def test_quoted_source_field_value_is_still_filtered():
    frame = framing_for("数据来源为官方版本页", resolution_source="官方版本页")
    assert frame.premises == []
    assert frame.retrieval_plan == []


def test_factual_model_inference_is_not_filtered_by_its_subject():
    frame = framing_for("数据来源存在伪造风险", origin="model_inferred")
    assert len(frame.premises) == 1
    assert frame.premises[0].origin == "model_inferred"


def test_mixed_premise_targets_survive_filtering_and_keep_candidate_indexes():
    claim = "数据来源已被监管机构判定造假"
    request = AnalyzeQuestionRequest(question={
        "question": f"既然{claim}，该公司能否完成审计？",
        "mode": "scenario",
        "as_of": "2026-09-30T08:00:00Z",
    })
    candidate = FramingCandidate(
        proposed_spec=request.question,
        premises=[
            {"content": "研究对象是该公司完成审计。", "origin": "user_explicit",
             "source_input_id": "I001", "original_span": "该公司能否完成审计"},
            {"content": claim, "origin": "user_explicit",
             "source_input_id": "I001", "original_span": claim},
        ],
        retrieval_plan=[
            {"query": "核查监管处罚", "purpose": "challenge", "premise_indexes": [0, 1]},
            {"query": "该公司审计", "purpose": "initial", "premise_indexes": [0]},
            {"query": "审计背景", "purpose": "background", "premise_indexes": []},
        ],
    )
    frame = finalize_framing(candidate, request, None)
    assert [p.id for p in frame.premises] == ["P001"]
    assert [p.content for p in frame.premises] == [claim]
    assert [task.query for task in frame.retrieval_plan] == ["核查监管处罚", "审计背景"]
    assert [task.target_premise_ids for task in frame.retrieval_plan] == [["P001"], []]


@pytest.mark.parametrize("claim", [
    "存在一家尚未注册的公司",
    "该研究对象存在重大财务错报",
    "该问题关注的公司已经停业",
])
def test_verbatim_explicit_facts_survive_other_restatement_patterns(claim):
    frame = framing_for(claim, lead="假设")
    assert [premise.content for premise in frame.premises] == [claim]
    assert frame.retrieval_plan[0].target_premise_ids == ["P001"]


def test_literal_problem_statement_equal_to_spec_is_still_filtered():
    question = "该问题关注公司能否完成审计"
    request = AnalyzeQuestionRequest(question={
        "question": question, "mode": "scenario", "as_of": "2026-09-30T08:00:00Z"})
    candidate = FramingCandidate(proposed_spec=request.question, premises=[{
        "content": question, "origin": "user_explicit",
        "source_input_id": "I001", "original_span": question}],
        retrieval_plan=[{"query": "公司审计", "premise_indexes": [0], "purpose": "initial"}])
    frame = finalize_framing(candidate, request, None)
    assert frame.premises == []
    assert frame.retrieval_plan == []
