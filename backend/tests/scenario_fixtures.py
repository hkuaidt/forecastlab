"""Explicit narrative fixture data for the scenario output contract."""
from copy import deepcopy


def scenario_details(probabilities, evidence_id="E001"):
    return [{"name":name, "definition":f"测试项目处于{name}路径，按验收结果与其他路径区分。",
             "conditions":[f"满足{name}路径的测试触发条件"],
             "rationale":"这是固定测试的主观分配，依据测试来源；条件变化会改变判断。",
             "evidence_ids":[evidence_id], "assumption_ids":[], "simulation_ids":[]}
            for name in probabilities]


def scenario_output(body):
    body = deepcopy(body)
    if body.get("probabilities") and "scenario_details" not in body:
        body["scenario_details"] = scenario_details(body["probabilities"])
    return body
