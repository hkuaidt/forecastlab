"""Simulation rounds cannot become mutually exclusive forecast end states."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from app import graph
from app.schemas import Evidence, Forecast, QuestionSpec, Review, RunRecord, SimulationStep, WorldState
from scenario_fixtures import scenario_details
from test_forecast_finding_references import frozen_state


def real_candidates():
    return json.loads((Path(__file__).parent / "fixtures/copied_round_forecasts_run_0602.json").read_text())["candidates"]


def inputs():
    state = frozen_state()
    return (state, QuestionSpec.model_validate(state["question"]),
        [Evidence.model_validate(e) for e in state["evidence"]], WorldState.model_validate(state["world"]),
        [SimulationStep.model_validate(s) for s in state["simulation"]], Review.model_validate(state["review"]))


def terminal_report(probabilities=None):
    probabilities = probabilities or {"制度化采用":.5, "局部辅助":.3, "维持原流程":.2}
    details = scenario_details(probabilities)
    for detail in details:
        detail["definition"] = "截至2027年底，按期刊对工具的采用程度判断：" + detail["name"] + "，与其余采用状态分开。"
        detail["conditions"] = ["编辑机构是否完成并公开采用决策", "若条件同时满足，以最终正式规则的采用程度判定"]
    details[0]["rationale"] = "在S1模拟路径下，主体行动提供采用起点；仍须机构实际决定，故只是主观终局判断。"
    return Forecast(status="completed", conclusion="同一目标时点的采用状态可能分化。",
                    probabilities=probabilities, scenario_details=details)


@pytest.mark.parametrize("index", [0,1])
def test_actual_two_candidates_are_rejected_after_valid_finding_ids_are_resolved(index):
    state, question, evidence, world, simulation, review = inputs()
    report = Forecast.model_validate(real_candidates()[index])
    graph.canonicalize_forecast_ids(report, evidence, world, simulation, state["evidence_assessment"])
    assert all(not eid.startswith("F") for claim in report.supporting for eid in claim.evidence_ids)
    with pytest.raises(ValueError, match="同一轨迹的先后状态"):
        graph.validate_forecast(report, question, evidence, world, simulation, review)


@pytest.mark.parametrize("defect", ["key", "name", "copied_definition"])
def test_literal_round_identity_checks_are_independent_and_ignore_whitespace(defect):
    _, _, _, _, simulation, _ = inputs()
    report = terminal_report()
    if defect == "key":
        value = report.probabilities.pop("制度化采用")
        report.probabilities[simulation[0].id] = value
    elif defect == "name":
        report.scenario_details[0].name = simulation[0].id
    else:
        report.scenario_details[0].definition = "  " + simulation[0].summary + "\n"
    with pytest.raises(ValueError, match="需修改字段"):
        graph.validate_scenario_end_states(report, simulation)


@pytest.mark.parametrize("probabilities", [{"顺利":.6,"受限":.4}, {"制度化采用":.5,"局部辅助":.3,"维持原流程":.2}])
def test_valid_two_or_three_named_end_states_keep_subjective_probability_and_only_used_s(probabilities):
    state, question, evidence, world, simulation, review = inputs()
    report = terminal_report(probabilities)
    graph.canonicalize_forecast_ids(report, evidence, world, simulation, state["evidence_assessment"])
    graph.validate_forecast(report, question, evidence, world, simulation, review, require_probability=True)
    assert report.status == "completed" and report.probabilities == probabilities
    assert report.scenario_details[0].simulation_ids == ["S1"]
    assert all(detail.simulation_ids == [] for detail in report.scenario_details[1:])


def test_only_explicit_real_simulation_ids_are_added_and_existing_refs_are_preserved():
    state, _, evidence, world, simulation, _ = inputs()
    report = terminal_report()
    report.scenario_details[0].simulation_ids = ["S2"]
    report.scenario_details[1].rationale = "在S10和XS1系统下的名称不代表S1a，也不代表未知S999。"
    report.scenario_details[2].rationale = "在S2模拟路径下，仍可能由于后续规则未采用而维持原流程。"
    graph.canonicalize_forecast_ids(report, evidence, world, simulation, state["evidence_assessment"])
    assert report.scenario_details[0].simulation_ids == ["S2", "S1"]
    assert report.scenario_details[1].simulation_ids == []
    assert report.scenario_details[2].simulation_ids == ["S2"]


def test_graph_corrects_copied_rounds_with_existing_second_attempt_and_keeps_raw_audit(tmp_path):
    state, question, *_ = inputs()
    original = deepcopy(state)
    bad = real_candidates()[0]
    good = terminal_report().model_dump()
    class Model:
        calls = 0
        def complete(self, role, payload, schema, instructions):
            assert role == "forecast"
            assert "同一目标日期和范围" in instructions and "固定一个可判断结果的轴" in instructions
            assert "2–3个具名终态" in instructions
            assert "各自条件成功率" in instructions
            assert "只登记该条实际使用的E/H/S" in instructions
            self.calls += 1
            if self.calls == 1:
                return schema.model_validate(bad)
            assert "同一轨迹的先后状态" in payload["validation_feedback"]
            return schema.model_validate(good)
    model = Model()
    record = RunRecord(run_id="rounds-are-not-outcomes", question=question, evidence_mode="import", model="fixture")
    result = graph.build_graph(record, [], model, tmp_path, start_at="synthesize").invoke(state)
    assert model.calls == 2
    assert result["forecast"]["status"] == "completed"
    assert result["forecast"]["probabilities"] == good["probabilities"]
    assert result["forecast"]["scenario_details"][0]["simulation_ids"] == ["S1"]
    assert record.forecast_attempts[0].candidate.model_dump(mode="json") == Forecast.model_validate(bad).model_dump(mode="json")
    assert record.forecast_attempts[0].validation_errors
    assert record.forecast_attempts[1].candidate.scenario_details[0].simulation_ids == []
    assert state == original
