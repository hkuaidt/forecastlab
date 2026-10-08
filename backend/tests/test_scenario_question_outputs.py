"""Real framing regression: requested research outputs are not user-provided facts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from app.agents.question import finalize_framing
from app.question_service import QuestionService
from app.schemas import AnalyzeQuestionRequest, ConfirmQuestionRequest, FramingCandidate, QuestionFraming
from app.storage import RunStore


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/scenario_output_framing.json").read_text())


def candidate(raw):
    premise_keys = {"content", "origin", "source_input_id", "original_span", "rationale", "replaces_id"}
    indexes = {p["id"]: i for i,p in enumerate(raw["premises"])}
    return FramingCandidate(proposed_spec=raw["proposed_spec"],
        clarifications=[{k:c[k] for k in ("field", "question", "blocking")} for c in raw["clarifications"]],
        premises=[{k:v for k,v in p.items() if k in premise_keys} for p in raw["premises"]],
        alternative_directions=raw["alternative_directions"],
        retrieval_plan=[{"query": t["query"], "purpose": t["purpose"],
            "premise_indexes": [indexes[p] for p in t["target_premise_ids"]]} for t in raw["retrieval_plan"]])


def test_actual_output_requirements_are_not_blocking_premises_or_search_targets(tmp_path):
    raw = fixture(); output = candidate(raw)
    calls = []
    class Model:
        def complete(self, *args, **kwargs):
            calls.append(args)
            return output.model_copy(deep=True)
    svc = QuestionService(RunStore(tmp_path), model_factory=lambda **kwargs: Model())
    frame = svc.analyze(AnalyzeQuestionRequest(question=raw["proposed_spec"]))
    assert len(calls) == 1
    assert frame.status == "ready_for_confirmation" and not frame.clarifications
    assert frame.premises == []
    assert len(frame.retrieval_plan) == 2
    assert all(t.purpose == "background" and not t.target_premise_ids for t in frame.retrieval_plan)
    assert "数学科研" in frame.retrieval_plan[0].query
    assert all("三个互斥" not in t.query for t in frame.retrieval_plan)
    assert frame.proposed_spec.mode == "scenario"
    assert svc.confirm(frame.draft_id, ConfirmQuestionRequest(expected_revision=1, decisions=[])).framing == frame
    assert "后续研究应产出" in calls[0][3]


def test_existing_blocked_draft_can_be_reanalyzed_without_answers(tmp_path):
    raw = fixture()
    previous = QuestionFraming(**raw, draft_id="draft_legacy_outputs", revision=1,
        inputs=[{"input_id":"I001","kind":"original","text":raw["raw_question"]}],
        status="needs_clarification", next_premise_number=6)
    store = RunStore(tmp_path); store.create_draft(previous)
    class Model:
        def complete(self, *args, **kwargs): return candidate(raw)
    svc = QuestionService(store, model_factory=lambda **kwargs: Model())
    result = svc.analyze(AnalyzeQuestionRequest(question=previous.proposed_spec,
        draft_id=previous.draft_id, expected_revision=1))
    assert result.revision == 2 and result.status == "ready_for_confirmation" and not result.premises
    assert store.get_revision(previous.draft_id, 1) == previous


@pytest.mark.parametrize("fact", ["AI最近取得重要发展", "研究提出了新的证明检验方法", "数据来源已被监管机构判定造假", "提供方已经停止发布数据", "输出结果存在重大错误", "说明书已经修订", "预测模型已经通过测试", "研究投入增加了20%", "研究人员减少", "分析结果显示成本下降"])
def test_actual_user_facts_survive_output_instruction_filter(fact):
    raw=fixture(); req=AnalyzeQuestionRequest(question={**raw["proposed_spec"],"question":f"既然{fact}，AI将怎样影响数学科研？请给出三个情景和概率。"})
    output=FramingCandidate(proposed_spec=req.question, premises=[
        {"content":fact,"origin":"user_explicit","source_input_id":"I001","original_span":fact},
        {"content":"需要给出三个情景和概率", "origin":"user_explicit", "source_input_id":"I001", "original_span":"请给出三个情景和概率"}],
        retrieval_plan=[{"query":"核查AI研究原始进展","purpose":"initial","premise_indexes":[0]},
            {"query":"三个互斥且覆盖主要可能性的情景、判定条件和概率","purpose":"initial","premise_indexes":[1]}])
    result=finalize_framing(output,req,None)
    assert [p.content for p in result.premises] == [fact]
    assert result.retrieval_plan[0].target_premise_ids == ["P001"]


def test_real_scope_ambiguity_remains_blocking_but_output_question_does_not():
    raw=fixture(); req=AnalyzeQuestionRequest(question=raw["proposed_spec"]); output=candidate(raw)
    output.clarifications[0].field="scope"
    output.clarifications[0].question="研究对象是中国期刊还是全球期刊？"
    output.clarifications[1].field="scope"
    output.clarifications[1].question="请给出每个情景的概率评估依据。"
    result=finalize_framing(output,req,None)
    assert result.status == "needs_clarification"
    assert len(result.clarifications) == 1 and "中国期刊" in result.clarifications[0].question


def test_only_output_search_plan_gets_subject_background_query_and_user_mode_wins():
    raw=fixture(); req=AnalyzeQuestionRequest(question=raw["proposed_spec"]); output=candidate(raw)
    output.retrieval_plan = output.retrieval_plan[-1:]
    output.proposed_spec.mode="binary"
    result=finalize_framing(output,req,None)
    assert result.status == "ready_for_confirmation" and result.proposed_spec.mode == "scenario"
    assert len(result.retrieval_plan) == 1 and result.retrieval_plan[0].purpose == "background"
    query=result.retrieval_plan[0].query
    assert "数学科研" in query and "三个互斥" not in query and "列出公开证据" not in query
