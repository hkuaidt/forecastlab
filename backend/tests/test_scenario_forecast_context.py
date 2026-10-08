from scenario_fixtures import scenario_details
"""Scenario reporting must not promote model trajectories into observed evidence."""
from copy import deepcopy
import json

import pytest
from app import graph
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.schemas import Claim, Forecast, QuestionSpec, Review, RunRecord, SimulationStep, WorldState


def scenario_state():
    question = QuestionSpec(question="该项目到2027年底会如何发展？", mode="scenario")
    sources = [e.model_dump(mode="json") for e in demo_evidence()]
    sources[0]["excerpt"] = "原文记录：该样本2026年完成率60%，不同于未来预测。"
    world = demo_output("world")
    world["summary"] = "未来形式化率30%-50%"
    world["assumptions"][0]["content"] = "模型假设提升20个百分点"
    world["actors"][0]["resources"] = ["2026-10-07可用资源占比60%"]
    action = demo_output("actor", "A001")
    action["action"] = "到2027年底完成率30–50%"
    step = demo_output("environment")
    step["summary"] = "模型计算完成率60%"
    step["state_changes"] = {"formalization": "百分之六十"}
    review = demo_output("review")
    review["issues"][0]["claim"] = "完成率达到60%"
    review["issues"][0]["explanation"] = "模型给出30%-50%，没有测量依据。"
    assessment = {"summary": "来源覆盖", "findings_validated": True, "findings": [], "evidence_ids": ["E001"]}
    return {"question": question.model_dump(mode="json"), "evidence": sources,
            "evidence_assessment": assessment, "world": world, "actions": [action],
            "simulation": [step], "review": review}


def test_private_mask_preserves_sources_dates_ids_and_original_trajectory():
    payload = scenario_state()
    payload["validation_feedback"] = "请修正 supporting[0]。"
    before = deepcopy(payload)
    masked = graph.scenario_forecast_context(payload)
    assert payload == before
    for key in ("evidence", "evidence_assessment", "validation_feedback"):
        assert masked[key] == payload[key]
    for key in ("world", "actions", "simulation", "review"):
        text = json.dumps(masked[key], ensure_ascii=False)
        assert not graph._REPORT_PERCENTAGE.search(text)
        assert graph._MASKED_MODEL_RATE in text
    assert masked["question"]["as_of"] == payload["question"]["as_of"]
    assert masked["question"]["outcomes"] == []
    assert "30–" not in masked["actions"][0]["action"]
    assert "2027" in masked["actions"][0]["action"]
    assert "2026-10-07" in masked["world"]["actors"][0]["resources"][0]
    assert masked["simulation"][0]["assumption_ids"] == ["H001"]
    assert masked["world"]["assumptions"][0]["parent_ids"] == ["E002"]
    assert masked["simulation"][0]["parent_ids"] == payload["simulation"][0]["parent_ids"]
    assert "不是新增观测" in masked["forecast_context_provenance"]["model_derived"]
    masked["evidence"][0]["excerpt"] = "mutated private copy"
    assert payload == before


@pytest.mark.parametrize("text", [
    "验证标准滞后导致进展停滞。",
    "根据原文，AI证明必须人工复核。",
    "模拟显示实际已确认验证标准不透明。",
    "若模型假设成立，流程可能受阻。实际已确认验证标准不透明。",
    "若验证流程改善，验证标准实际上不透明。",
])
def test_mixed_evidence_hypothesis_references_do_not_certify_facts(text):
    report = Forecast(status="scenario_only", conclusion="条件推演", supporting=[
        Claim(text=text, evidence_ids=["E001"], assumption_ids=["H001"], simulation_ids=["S1"])])
    with pytest.raises(ValueError, match=r"supporting\[0\]"):
        graph.validate_forecast_claim_kinds(report)


@pytest.mark.parametrize("text", [
    "若验证标准滞后，则可能限制进展；这一条件尚待检验。",
    "模型假设：若工具验证与专家判断无法协调，则可能增加复核时间。",
    "在H001假设成立条件下，后续验证可能延误。",
    "模拟：验证流程存在延误的可能路径。",
])
def test_explicit_conditional_claims_keep_traceable_hypotheses(text):
    report = Forecast(status="scenario_only", conclusion="条件推演", supporting=[
        Claim(text=text, evidence_ids=["E001"], assumption_ids=["H001"], simulation_ids=["S1"])])
    graph.validate_forecast_claim_kinds(report)


def test_quality_limitations_list_all_hypothesis_and_rate_fields_without_blocking():
    state = scenario_state()
    report = Forecast(status="scenario_only", conclusion="条件推演", supporting=[
        Claim(text="形式化率30%-50%", assumption_ids=["H001"])], opposing=[
        Claim(text="实际验证标准滞后", simulation_ids=["S1"])], scenarios=["自动化率60%"], limitations=["覆盖率40%"])
    graph.validate_forecast(report, QuestionSpec.model_validate(state["question"]), demo_evidence(),
                            WorldState.model_validate(state["world"]),
                            [SimulationStep.model_validate(state["simulation"][0])], Review(status="qualified"))
    warning = " ".join(report.limitations)
    for field in ("supporting[0]", "opposing[0]", "scenarios[0]", "limitations[0]"):
        assert field in warning
    assert "质量提示" in warning



@pytest.mark.parametrize("mode", ["scenario", "binary"])
def test_actual_forecast_model_input_is_private_and_scoped_to_scenario(mode, tmp_path):
    state = scenario_state()
    if mode == "binary":
        state["question"] = DEMO_QUESTION.model_dump(mode="json")
    before = deepcopy(state)
    class CaptureModel:
        def complete(self, role, payload, schema, instructions):
            assert role == "forecast"
            self.payload = deepcopy(payload)
            self.instructions = instructions
            return schema.model_validate({"status": "completed", "conclusion": "若复核完成，则可能推进。", "probabilities": {"是": .6, "否": .4}, "scenario_details": scenario_details({"是": .6, "否": .4}),
                "supporting": [{"text": "若验证条件具备，则可能推进。", "evidence_ids": ["E001"], "assumption_ids": ["H001"]}]})
    model = CaptureModel()
    record = RunRecord(run_id="report_input_fixture", question=QuestionSpec.model_validate(state["question"]),
                       evidence_mode="import", model="fixture")
    compiled = graph.build_graph(record, [], model, tmp_path, start_at="synthesize")
    result = compiled.invoke(state)
    assert result["forecast"]["supporting"][0]["text"] == "若验证条件具备，则可能推进。"
    assert state == before
    if mode == "scenario":
        assert graph._MASKED_MODEL_RATE in model.payload["world"]["summary"]
        assert graph._MASKED_MODEL_RATE in model.payload["simulation"][0]["summary"]
        assert not graph._REPORT_PERCENTAGE.search(json.dumps({k:model.payload[k] for k in ("world", "actions", "simulation", "review")}, ensure_ascii=False))
    else:
        assert model.payload["world"]["summary"] == before["world"]["summary"]
        assert "forecast_context_provenance" not in model.payload
    assert model.payload["evidence"][0]["excerpt"] == before["evidence"][0]["excerpt"]
    assert "60%" in model.payload["evidence"][0]["excerpt"]
    assert "区分来源事实" in model.instructions
