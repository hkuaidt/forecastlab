"""Confirmed scope reaches each model without becoming an external evidence claim."""
from copy import deepcopy
import json
from pathlib import Path

from app import graph
from app.agents.evidence import active_framing
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.schemas import QuestionFraming, QuestionSpec, RunRecord
from scenario_fixtures import scenario_output
from test_scenario_forecast_context import scenario_state


def framing():
    raw=json.loads((Path(__file__).parent / "fixtures/answered_scope_clarification.json").read_text())
    frame=QuestionFraming.model_validate(raw["returned_revision2"])
    frame.clarifications[0].answer=raw["answer"]
    frame.clarifications[0].status="resolved"
    frame.premises=[]
    frame.status="ready_for_confirmation"
    return frame


def test_resolved_scope_survives_every_live_graph_role(tmp_path):
    frame=framing(); original=frame.model_dump_json(); roles=[]
    class Capture:
        def complete(self,role,payload,schema,instructions,**kwargs):
            roles.append(role)
            answers=payload["question_framing"]["clarification_answers"]
            assert len(answers)==1 and answers[0]["answer"]==frame.clarifications[0].answer
            assert "Lean" in answers[0]["answer"] and "评审分流" in answers[0]["answer"]
            assert "用户确认的研究范围" in instructions
            assert payload["question_framing"]["premises"]==[]
            if role=="evidence12":
                return schema.model_validate({"summary":"已保存来源","findings":[]})
            actor=payload.get("actor",{}).get("id")
            output=demo_output(role,actor,payload.get("round",1))
            return schema.model_validate(scenario_output(output) if role=="forecast" else output)
    question=QuestionSpec.model_validate(frame.proposed_spec.model_dump())
    record=RunRecord(run_id="scope_context",question=question,evidence_mode="import",model="fixture",question_framing=frame)
    result=graph.build_graph(record,demo_evidence(),Capture(),tmp_path).invoke({"question":question.model_dump(mode="json")})
    assert set(roles)=={"evidence12","world","actor","environment","review","forecast"}
    assert result["forecast"]["status"]=="completed"
    assert frame.model_dump_json()==original


def test_unresolved_answer_is_not_presented_as_confirmed_scope():
    frame=framing(); frame.clarifications[0].status="open"
    assert active_framing(frame)["clarification_answers"]==[]


def test_evidence_only_branch_keeps_scope_without_reintroducing_premises(tmp_path):
    frame=framing(); state=scenario_state()
    state["question"]=DEMO_QUESTION.model_dump(mode="json")
    state["review"].update(probability_basis="evidence_only",evidence_audit={"can_estimate":True})
    class Capture:
        def complete(self,role,payload,schema,instructions):
            assert role=="forecast"
            assert payload["clarification_answers"][0]["answer"]==frame.clarifications[0].answer
            assert "question_framing" not in payload and "world" not in payload
            output=demo_output(role)
            output["supporting"]=[];output["opposing"]=[];output["key_assumptions"]=[]
            output["probability_basis"]="evidence_only"
            return schema.model_validate(output)
    record=RunRecord(run_id="scope_evidence_only",question=DEMO_QUESTION,evidence_mode="import",model="fixture",question_framing=frame)
    result=graph.build_graph(record,[],Capture(),tmp_path,start_at="synthesize").invoke(state)
    assert result["forecast"]["status"]=="completed"
