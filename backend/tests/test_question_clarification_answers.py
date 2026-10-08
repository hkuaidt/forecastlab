"""Answered clarification identity and scope regression from two real revisions."""
from copy import deepcopy
import json
from pathlib import Path

from app.agents.question import finalize_framing
from app.question_service import QuestionService
from app.schemas import AnalyzeQuestionRequest, ClarificationCandidate, ConfirmQuestionRequest, FramingCandidate, QuestionFraming
from app.storage import RunStore


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/answered_scope_clarification.json").read_text())


def candidate(frame):
    fields = {"content","origin","source_input_id","original_span","rationale","replaces_id"}
    index = {p["id"]:i for i,p in enumerate(frame["premises"])}
    return FramingCandidate(proposed_spec=frame["proposed_spec"],
        clarifications=[{k:c[k] for k in ("field","question","blocking")} for c in frame["clarifications"]],
        premises=[{k:v for k,v in p.items() if k in fields} for p in frame["premises"]],
        alternative_directions=frame["alternative_directions"], retrieval_plan=[
            {"query":t["query"],"purpose":t["purpose"],"premise_indexes":[index[p] for p in t["target_premise_ids"]]}
            for t in frame["retrieval_plan"]])


def test_actual_two_revisions_merge_answer_and_scope_without_another_model_round(tmp_path):
    raw=fixture(); outputs=[candidate(raw["revision1"]),candidate(raw["returned_revision2"])]
    class Model:
        def complete(self,*args,**kwargs): return outputs.pop(0)
    store=RunStore(tmp_path); service=QuestionService(store,model_factory=lambda **kwargs:Model())
    first=service.analyze(AnalyzeQuestionRequest(question=raw["revision1"]["proposed_spec"]))
    assert first.status == "needs_clarification" and first.clarifications[0].id == "C001"
    request=AnalyzeQuestionRequest(question=first.proposed_spec,draft_id=first.draft_id,expected_revision=1,
        answers=[{"clarification_id":"C001","answer":raw["answer"]}])
    second=service.analyze(request)
    assert not outputs
    assert second.revision == 2 and second.status == "ready_for_confirmation"
    assert len(second.clarifications) == 1
    assert second.clarifications[0].id == "C001" and second.clarifications[0].status == "resolved"
    assert second.clarifications[0].answer == raw["answer"]
    assert second.inputs[-1].text == raw["answer"] and not second.premises
    assert len(second.retrieval_plan) == 3
    assert store.get_revision(first.draft_id,1) == first
    confirmed=service.confirm(first.draft_id,ConfirmQuestionRequest(expected_revision=2,decisions=[]))
    assert confirmed.framing.clarifications[0].answer == raw["answer"]


def test_new_question_in_same_field_gets_new_id_and_remains_open():
    raw=fixture(); previous=QuestionFraming.model_validate(raw["revision1"])
    output=candidate(raw["returned_revision2"])
    output.clarifications=[ClarificationCandidate(field="研究对象",question="是否只讨论中国的科研机构？",blocking=True)]
    request=AnalyzeQuestionRequest(question=previous.proposed_spec,draft_id=previous.draft_id,expected_revision=1,
        answers=[{"clarification_id":"C001","answer":raw["answer"]}])
    second=finalize_framing(output,request,previous)
    assert second.status == "needs_clarification"
    rows={c.id:c for c in second.clarifications}
    assert rows["C001"].status == "resolved" and rows["C001"].answer == raw["answer"]
    assert rows["C002"].status == "open" and rows["C002"].answer is None
    third=finalize_framing(output,AnalyzeQuestionRequest(question=second.proposed_spec,
        draft_id=second.draft_id,expected_revision=2),second)
    assert {c.question:c.id for c in third.clarifications} == {c.question:c.id for c in second.clarifications}
    assert third.status == "needs_clarification"


def test_answered_question_stays_resolved_when_model_repeats_or_omits_it():
    raw=fixture(); previous=QuestionFraming.model_validate(raw["revision1"])
    req=AnalyzeQuestionRequest(question=previous.proposed_spec,draft_id=previous.draft_id,expected_revision=1,
        answers=[{"clarification_id":"C001","answer":raw["answer"]}])
    second=finalize_framing(candidate(raw["returned_revision2"]),req,previous)
    for repeated in (True,False):
        output=candidate(raw["returned_revision2"])
        if not repeated: output.clarifications=[]
        result=finalize_framing(output,AnalyzeQuestionRequest(question=second.proposed_spec,
            draft_id=second.draft_id,expected_revision=2),second)
        assert result.status == "ready_for_confirmation"
        assert result.clarifications[0].id == "C001" and result.clarifications[0].status == "resolved"
        assert result.clarifications[0].answer == raw["answer"]
