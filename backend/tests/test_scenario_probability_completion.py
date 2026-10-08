from scenario_fixtures import scenario_details
"""Evidence-backed scenarios prioritize valid subjective probability output."""
import pytest
from pydantic import ValidationError
from app import graph
from app.demo import demo_evidence, demo_output
from app.report_repair import prepare_report_repair, report_repair_info
from app.schemas import Claim, Forecast, QuestionSpec, Review, ScenarioForecast, SimulationStep, WorldState
from app.storage import RunStore
from test_report_failure_recovery import saved_report, ReportModel, GOOD, BAD


def validate(report, *, evidence=None):
    return graph.validate_forecast(report, QuestionSpec(question="项目未来进展如何演变？", mode="scenario"),
        demo_evidence() if evidence is None else evidence, WorldState.model_validate(demo_output("world")),
        [SimulationStep.model_validate(demo_output("environment"))], Review(status="blocked", probability_basis="none"))


def test_required_scenario_schema_cannot_omit_or_null_probabilities():
    for body in ({"conclusion":"结果"}, {"conclusion":"结果", "probabilities":None}):
        with pytest.raises(ValidationError):
            ScenarioForecast.model_validate(body)
    report = ScenarioForecast(conclusion="结果", probabilities={"基准":.5,"加速":.3,"受限":.2}, scenario_details=scenario_details({"基准":.5,"加速":.3,"受限":.2}))
    assert report.status == "completed" and not report.calibrated


def test_blocked_review_hypotheses_and_model_rates_are_limitations_not_probability_gates():
    report = Forecast(status="scenario_only", conclusion="路径存在分化", probabilities={"基准":.5,"加速":.3,"受限":.2},
        supporting=[Claim(text="形式化率30%-50%", evidence_ids=["E001"], assumption_ids=["H001"], simulation_ids=["S1"])])
    validate(report)
    assert report.status == "completed" and report.probabilities == {"基准":.5,"加速":.3,"受限":.2}
    assert any("未经校准" in note for note in report.limitations)
    assert any("审查仍有" in note for note in report.limitations)
    assert any("质量提示" in note for note in report.limitations)


@pytest.mark.parametrize("probabilities", [{"A":.8,"B":.8}, {"A":-.1,"B":1.1}, {"A":float('nan'),"B":.5}, {"A":1}, {"":.5,"B":.5}])
def test_scenario_probabilities_keep_basic_numeric_and_named_outcome_checks(probabilities):
    with pytest.raises(ValueError):
        validate(Forecast(status="completed", conclusion="结果", probabilities=probabilities))


def test_source_ids_still_must_exist_and_no_source_cannot_receive_probabilities():
    report = Forecast(status="completed", conclusion="结果", probabilities={"A":.5,"B":.5},
        supporting=[Claim(text="未知来源", evidence_ids=["E999"])])
    with pytest.raises(ValueError, match="不存在"):
        validate(report)
    report.supporting = []
    validate(report, evidence=[])
    assert report.probabilities is None and report.status != "completed"


def test_partial_source_fallback_can_create_another_bounded_report_repair(tmp_path, monkeypatch):
    parent = saved_report(tmp_path)
    model = ReportModel([BAD, BAD])
    monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: model)
    graph.execute(parent, parent.evidence, RunStore(tmp_path), resume=True)
    assert parent.status == "partial" and parent.forecast.probabilities is None
    assert report_repair_info(parent)["available"]
    before = parent.model_dump_json()
    child = prepare_report_repair(parent, tmp_path)
    assert parent.model_dump_json() == before
    assert child.report_repair_audit["new_call_limit"] == 2 and child.report_repair_parent == parent.run_id
    model.outputs = iter([GOOD])
    graph.execute(child, child.evidence, RunStore(tmp_path), resume=True)
    assert child.status == "completed" and child.forecast.probabilities == GOOD["probabilities"]


def test_schema_failures_return_traceable_partial_without_fabricated_probability(tmp_path, monkeypatch):
    parent = saved_report(tmp_path)
    invalid = {"status":"completed", "conclusion":"模型遗漏了概率"}
    model = ReportModel([invalid, invalid])
    monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: model)
    graph.execute(parent, parent.evidence, RunStore(tmp_path), resume=True)
    assert model.roles == ["forecast", "forecast"]
    assert parent.status == "partial" and parent.forecast.probabilities is None
    assert parent.forecast.supporting[0].evidence_ids == ["E001"]
    assert report_repair_info(parent)["available"]
