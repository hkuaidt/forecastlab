"""The same report call defines terminal outcomes before assigning their weights."""
from copy import deepcopy
import json

import pytest
from app import config, graph
from app.forecast_wire import DefinitionFirstForecast, to_public_forecast, review_first_view
from app.schemas import Forecast, QuestionSpec, RunRecord
from test_forecast_finding_references import frozen_state
from test_scenario_end_states import inputs, real_candidates


def response(count=3):
    names = ["全面纳入流程", "部分环节纳入", "继续辅助使用"][:count]
    definitions = ["到2027年底，所讨论各环节均形成正式可复核流程。",
        "到2027年底，至少一个但不是全部环节形成正式可复核流程。",
        "到2027年底，没有环节形成正式流程，仍以个别辅助使用为主。"][:count]
    if count == 2:
        names[1], definitions[1] = "尚未全面纳入", "到2027年底，至少一个环节尚未形成正式可复核流程。"
    weights = [.43,.34,.23] if count==3 else [.57,.43]
    return {"terminal_target":{"target":"研究流程采用程度","horizon":"2027-12-31","scope":"用户指定的全球研究范围"},
        "terminal_axis":"目标时点各研究环节纳入正式流程的程度",
        "terminal_definitions":[{"outcome_id":f"outcome_{i+1}","name":name,"definition":definitions[i],
            "conditions":["机构形成明示采用决策","以目标时点正式流程的覆盖情况判定"]} for i,name in enumerate(names)],
        "terminal_weights":[{"outcome_id":f"outcome_{i+1}","weight":weights[i],
            "rationale":"在S1模拟路径下，合作可作为条件线索；正式采用仍待机构决定，原文没有证明资金机构政策。",
            "evidence_ids":["E001"],"simulation_ids":["S1"]} for i in range(count)],
        "conclusion":"未来采用程度取决于可复核结果与机构选择，现有资料不能证明流程改善已经实现。",
        "limitations":["所选出版规则不能代表独立资金机构政策；跨领域材料仅作参考。"]}


@pytest.mark.parametrize("count", [2,3])
def test_schema_places_all_definitions_before_weights_and_conversion_keeps_numbers(count):
    properties=list(DefinitionFirstForecast.model_json_schema()["properties"])
    assert properties.index("terminal_target") < properties.index("terminal_axis") < properties.index("terminal_definitions") < properties.index("terminal_weights")
    body=response(count); before=deepcopy(body)
    wire=DefinitionFirstForecast.model_validate_json(json.dumps(body))
    report=to_public_forecast(wire)
    assert list(report.probabilities) == [item["name"] for item in body["terminal_definitions"]]
    assert list(report.probabilities.values()) == [item["weight"] for item in body["terminal_weights"]]
    _,question,evidence,world,simulation,review=inputs()
    graph.validate_forecast(report,question,evidence,world,simulation,review,require_probability=True)
    assert body==before and report.status=="completed"


@pytest.mark.parametrize("index", [0,1])
def test_legacy_s_round_candidates_are_never_renamed_or_reweighted(index):
    original=real_candidates()[index]
    candidate=DefinitionFirstForecast.model_validate(original)
    assert isinstance(candidate,Forecast)
    report=to_public_forecast(candidate)
    assert report.model_dump(mode="json")==original
    with pytest.raises(ValueError,match="先后状态"):
        graph.validate_scenario_end_states(report,inputs()[4])


@pytest.mark.parametrize("damage", ["missing_slot","duplicate_name","unknown_e"])
def test_bad_mapping_or_unknown_reference_is_not_repaired(damage):
    body=response()
    if damage=="missing_slot":
        body["terminal_weights"][1]["outcome_id"]="outcome_1"
    elif damage=="duplicate_name":
        body["terminal_definitions"][1]["name"]=body["terminal_definitions"][0]["name"]
    else:
        body["terminal_weights"][0]["evidence_ids"]=["E999"]
    wire=DefinitionFirstForecast.model_validate(body)
    with pytest.raises(ValueError):
        report=to_public_forecast(wire)
        _,question,evidence,world,simulation,review=inputs()
        graph.validate_forecast(report,question,evidence,world,simulation,review)


def test_graph_uses_one_wire_call_preserves_raw_audit_and_manual_feedback(tmp_path):
    state=frozen_state(); original=deepcopy(state); body=response()
    class Model:
        calls=0
        def complete(self,role,payload,schema,instructions):
            self.calls+=1
            assert schema is DefinitionFirstForecast
            assert payload["terminal_task"]["output_order"][2:4]==["terminal_definitions","terminal_weights"]
            assert "来源适用范围" in payload["validation_feedback"]
            assert "原文优先于F模型释义" in instructions
            assert "保留未决条件" in instructions
            return schema.model_validate(body)
    model=Model()
    record=RunRecord(run_id="definition-first",question=QuestionSpec.model_validate(state["question"]),evidence_mode="import",model="fixture",
        report_repair_audit={"validation_feedback":"检查来源适用范围，资金政策没有直接证据。"})
    result=graph.build_graph(record,[],model,tmp_path,start_at="synthesize").invoke(state)
    assert model.calls==1 and result["forecast"]["status"]=="completed"
    assert record.forecast_policy["wire_attempts"][0]["candidate"]==DefinitionFirstForecast.model_validate(body).model_dump(mode="json")
    assert record.forecast_policy["wire_attempts"][0]["validation_errors"]==[]
    assert record.forecast_attempts[0].candidate.probabilities==result["forecast"]["probabilities"]
    assert state==original


def test_rejected_wire_still_has_raw_audit_before_existing_second_attempt(tmp_path):
    state=frozen_state(); bad=response(); bad["terminal_weights"][0]["evidence_ids"]=["E999"]
    class Model:
        calls=0
        def complete(self,role,payload,schema,instructions):
            self.calls+=1
            if self.calls==2:
                assert "E999" in payload["validation_feedback"]
            return schema.model_validate(bad if self.calls==1 else response())
    model=Model(); record=RunRecord(run_id="wire-repair",question=QuestionSpec.model_validate(state["question"]),evidence_mode="import",model="fixture")
    result=graph.build_graph(record,[],model,tmp_path,start_at="synthesize").invoke(state)
    assert model.calls==2 and result["forecast"]["status"]=="completed"
    assert record.forecast_policy["wire_attempts"][0]["candidate"]["terminal_weights"][0]["evidence_ids"]==["E999"]
    assert record.forecast_policy["wire_attempts"][0]["validation_errors"]


def test_binary_evidence_only_and_shadow_stay_on_their_existing_schemas(tmp_path,monkeypatch):
    state=frozen_state(); state["question"].update(mode="binary",outcomes=["是","否"],resolve_by="2027-12-31T00:00:00Z",resolution_rule="以官方发布记录判断是否完成")
    state["review"].update(status="passed",probability_basis="evidence_only")
    monkeypatch.setattr(config,"SHADOW_FULL",True)
    class Model:
        schemas=[]
        def complete(self,role,payload,schema,instructions):
            self.schemas.append(schema.__name__)
            return schema.model_validate({"status":"completed","conclusion":"依据已有资料作主观判断。",
                "probabilities":{"是":.55,"否":.45},"supporting":[{"text":"来源提供有限参考。","evidence_ids":["E001"]}]})
    model=Model(); record=RunRecord(run_id="binary-shadow-wire",question=QuestionSpec.model_validate(state["question"]),evidence_mode="import",model="fixture")
    result=graph.build_graph(record,[],model,tmp_path,start_at="synthesize").invoke(state)
    assert model.schemas==["EvidenceOnlyForecast","Forecast"]
    assert record.shadow_forecast and result["forecast"]["probability_basis"]=="evidence_only"


def test_private_review_view_marks_only_named_interpretations_and_keeps_exact_quotes():
    payload=frozen_state()
    payload["review"]["issues"]=[{"claim":"原释义超过原文支持范围", "explanation":"缺乏效果的直接依据。",
        "severity":"medium", "affected_ids":["F001","E001"]}]
    original=deepcopy(payload)
    view=review_first_view(payload)
    assert next(iter(view))=="report_first_read"
    assert view["report_first_read"]["review_constraints"]==[{"finding_ids":["F001"],"review_limitation":"缺乏效果的直接依据。"}]
    before={item["id"]:item for item in payload["evidence_assessment"]["findings"]}
    after={item["id"]:item for item in view["evidence_assessment"]["findings"]}
    assert after["F001"]["claim"].startswith("[disputed_interpretation")
    assert after["F001"]["claim"].endswith(before["F001"]["claim"])
    for fid in before:
        assert after[fid]["citations"]==before[fid]["citations"]
        if fid!="F001":
            assert after[fid]==before[fid]
    assert "claim" not in view["review"]["issues"][0]
    assert view["review"]["issues"][0]["challenged_claim"]=="原释义超过原文支持范围"
    assert view["evidence"]==payload["evidence"] and payload==original


def test_review_first_graph_input_and_sequential_boundary_prompt_add_no_call(tmp_path):
    state=frozen_state(); original=deepcopy(state)
    class Model:
        calls=0
        def complete(self,role,payload,schema,instructions):
            self.calls+=1
            assert next(iter(payload))=="report_first_read"
            assert "非A且B" in instructions and "剩余分支举例" in instructions
            assert "轴只选一种结果" in instructions and "conditions单列驱动条件" in instructions
            assert "supporting/opposing只要引用H或S" in instructions
            assert "原文实际记录" in instructions and "在H/S条件下可能" in instructions
            assert all("claim" not in issue for issue in payload["review"]["issues"])
            return schema.model_validate(response())
    model=Model(); record=RunRecord(run_id="review-first",question=QuestionSpec.model_validate(state["question"]),evidence_mode="import",model="fixture")
    result=graph.build_graph(record,[],model,tmp_path,start_at="synthesize").invoke(state)
    assert model.calls==1 and result["forecast"]["status"]=="completed" and state==original
