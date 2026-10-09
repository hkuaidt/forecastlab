"""New weights apply to a predeclared ordered partition; old forecasts stay intact."""
from copy import deepcopy
import pytest
from app import graph
from app.forecast_wire import DefinitionFirstForecast, WIRE_FORMAT, definition_first_task, to_public_forecast, qualify_model_claims
from app.schemas import Forecast
from test_definition_first_forecast import response
from test_scenario_end_states import inputs


@pytest.mark.parametrize("count", [2,3])
def test_ordered_partition_uses_slots_not_list_order_and_never_changes_weights(count):
    body=response(count); original=deepcopy(body)
    body["terminal_definitions"].reverse(); body["terminal_weights"].reverse()
    wire=DefinitionFirstForecast.model_validate(body); before=wire.model_dump()
    public=to_public_forecast(wire)
    a=original["terminal_definitions"][0]["definition"]
    assert public.scenario_details[0].definition.endswith(f"满足判据A：『{a}』。")
    if count==3:
        b=original["terminal_definitions"][1]["definition"]
        assert f"不满足判据A『{a}』，且满足判据B『{b}』" in public.scenario_details[1].definition
        assert f"既不满足判据A『{a}』，也不满足判据B『{b}』的全部剩余情况" in public.scenario_details[2].definition
    else:
        assert f"不满足判据A『{a}』的全部剩余情况" in public.scenario_details[1].definition
    last=public.scenario_details[-1]
    assert "模型对本分支的举例/倾向：" in last.definition and "不构成额外必要条件" in last.definition
    assert last.conditions==original["terminal_definitions"][-1]["conditions"]
    assert list(public.probabilities)==[item["name"] for item in original["terminal_definitions"]]
    assert list(public.probabilities.values())==[item["weight"] for item in original["terminal_weights"]]
    assert public.scenarios==[f"{item.name}：{item.definition}" for item in public.scenario_details]
    assert all("2027-12-31" in item.definition for item in public.scenario_details)
    assert wire.model_dump()==before


def test_contract_is_in_model_task_and_json_schema_before_generation():
    task=definition_first_task({"outcomes":[]})
    assert task["version"]==WIRE_FORMAT=="definition-first-specific-v3"
    assert task["partition_contract"]["three_slots"]==["outcome_1: A=definition1","outcome_2: NOT A AND B=definition2","outcome_3: NOT A AND NOT B"]
    assert task["partition_contract"]["two_slots"][-1]=="outcome_2: NOT A"
    schema=DefinitionFirstForecast.model_json_schema()
    assert "最终有序partition" in schema["$defs"]["TerminalWeight"]["properties"]["weight"]["description"]
    assert "仅剩余分支示例" in schema["$defs"]["TerminalDefinition"]["properties"]["definition"]["description"]


def test_h_s_claims_and_scenario_reasons_are_explicitly_conditional_but_raw_is_intact():
    body=response()
    body["supporting"]=[{"text":"合作已经保障准确性。","evidence_ids":["E001"],"assumption_ids":["H001"]},
                        {"text":"原文提供一项具体记录。","evidence_ids":["E001"]}]
    body["opposing"]=[{"text":"合作受阻。","simulation_ids":["S1"]}]
    wire=DefinitionFirstForecast.model_validate(body); raw=wire.model_dump()
    public=to_public_forecast(wire)
    assert public.supporting[0].text.startswith("若所引用的H/S条件成立")
    assert public.supporting[0].text.endswith(body["supporting"][0]["text"])
    assert public.opposing[0].text.startswith("若所引用的H/S条件成立")
    assert public.supporting[1].text==body["supporting"][1]["text"]
    assert all(item.rationale.startswith("模型条件解释") and "E仅提供背景" in item.rationale for item in public.scenario_details)
    assert all(item in public.limitations for item in body["limitations"])
    assert wire.model_dump()==raw
    before=public.model_dump(); qualify_model_claims(public); assert public.model_dump()==before


def test_invalid_or_noncontiguous_slot_is_not_silently_reinterpreted():
    body=response(2)
    body["terminal_definitions"][1]["outcome_id"]="outcome_3"
    body["terminal_weights"][1]["outcome_id"]="outcome_3"
    with pytest.raises(ValueError,match="连续"):
        to_public_forecast(DefinitionFirstForecast.model_validate(body))


def test_unknown_reference_still_rejected_and_old_completed_forecast_not_rewritten():
    body=response(); body["terminal_weights"][0]["evidence_ids"]=["E999"]
    public=to_public_forecast(DefinitionFirstForecast.model_validate(body))
    _,question,evidence,world,simulation,review=inputs()
    with pytest.raises(ValueError,match="E999"):
        graph.validate_forecast(public,question,evidence,world,simulation,review)
    old=Forecast(status="completed",conclusion="旧候选",probabilities={"旧终态甲":.4,"旧终态乙":.35,"旧终态丙":.25},
        supporting=[{"text":"旧的无条件句","assumption_ids":["H001"]}])
    assert to_public_forecast(old).model_dump()==old.model_dump()


@pytest.mark.parametrize("damage", ["name", "copied_definition"])
def test_raw_round_identity_is_rejected_before_contract_conversion_and_audited(tmp_path, damage):
    from app.schemas import QuestionSpec, RunRecord
    from test_forecast_finding_references import frozen_state
    state=frozen_state(); bad=response()
    step=state["simulation"][0]
    if damage=="name":
        bad["terminal_definitions"][0]["name"]=step["id"]
    else:
        bad["terminal_definitions"][0]["definition"]="  " + step["summary"] + "\n"
    class Model:
        calls=0
        def complete(self,role,payload,schema,instructions):
            self.calls+=1
            if self.calls==2:
                assert "terminal_definitions[0]" in payload["validation_feedback"]
            return schema.model_validate(bad if self.calls==1 else response())
    model=Model()
    record=RunRecord(run_id="raw-partition-guard",question=QuestionSpec.model_validate(state["question"]),evidence_mode="import",model="fixture")
    result=graph.build_graph(record,[],model,tmp_path,start_at="synthesize").invoke(state)
    assert model.calls==2 and result["forecast"]["status"]=="completed"
    audits=record.forecast_policy["wire_attempts"]
    assert audits[0]["candidate"]==DefinitionFirstForecast.model_validate(bad).model_dump(mode="json")
    assert "先后状态" in audits[0]["validation_errors"][0]
    assert not audits[1]["validation_errors"]
