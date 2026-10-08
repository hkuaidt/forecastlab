"""A rate cannot bypass validation by moving from scenarios into supporting text."""
import pytest
from app.graph import validate_forecast, validate_scenario_quantification
from app.schemas import (Claim, Evidence, EvidencePassage, Forecast, QuestionSpec, Review, WorldState)


@pytest.mark.parametrize("field", ["conclusion", "supporting", "opposing", "scenarios", "new_information", "limitations", "key_assumptions"])
@pytest.mark.parametrize("rate", ["30%-50%", "百分之六十", "提升20个百分点"])
def test_unattributed_scenario_rates_are_rejected_in_every_public_field(field, rate):
    forecast = Forecast(status="scenario_only", conclusion="定性条件推演。")
    text = f"基准情景：形式化率{rate}"
    if field == "conclusion": forecast.conclusion = text
    elif field in {"supporting", "opposing"}: setattr(forecast, field, [Claim(text=text, evidence_ids=["E001"])])
    else: setattr(forecast, field, [text])
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(forecast, [])


def test_model_rate_is_disclosed_as_quality_warning_with_valid_trace_ids():
    from app.demo import demo_evidence, demo_output
    from app.schemas import SimulationStep
    evidence = demo_evidence()
    world = WorldState.model_validate(demo_output("world"))
    simulation = [SimulationStep.model_validate(demo_output("environment", round_number=1))]
    forecast = Forecast(status="scenario_only", conclusion="多路径分化", supporting=[Claim(
        text="基准情景：形式化率提升至30%-50%", evidence_ids=["E001"], assumption_ids=["H001"], simulation_ids=["S1"])], scenarios=["基准情景"])
    question = QuestionSpec(question="数学科研未来将如何变化？", mode="scenario")
    validate_forecast(forecast, question, evidence, world, simulation, Review(status="blocked"))
    assert any("supporting[0]" in note and "质量提示" in note for note in forecast.limitations)


def test_percentages_marked_explicitly_as_unmeasured_hypotheses_are_allowed():
    forecast = Forecast(status="scenario_only", conclusion="定性结论", scenarios=["仅为假设参数示意：形式化率60%，无数据支撑，不代表预测。"])
    validate_scenario_quantification(forecast, [])


def test_conditional_word_alone_does_not_certify_an_arbitrary_rate():
    forecast = Forecast(status="scenario_only", conclusion="若H001假设成立，形式化率将突破60%。")
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(forecast, [])


def test_attributed_source_statistic_does_not_require_fake_hypothesis_label():
    from app.demo import demo_evidence
    source = demo_evidence()[0]
    source.passages = [EvidencePassage(paragraph_id="B000001", start=0, end=25, text="The observed rate was 60%.", snapshot_hash="f"*64)]
    forecast = Forecast(status="scenario_only", conclusion="条件结论", supporting=[Claim(text="来源报告：“The observed rate was 60%.”", evidence_ids=[source.id])])
    validate_scenario_quantification(forecast, [source])
    forecast.supporting[0].simulation_ids = ["S1"]
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(forecast, [source])


@pytest.mark.parametrize("separator", ["。", ";", "；", "\n", ". "])
def test_hypothesis_disclaimer_does_not_license_another_sentence(separator):
    forecast = Forecast(status="scenario_only", conclusion=f"仅为假设参数示意：形式化率30%{separator}加速情景下形式化率突破60%。")
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(forecast, [])


def test_coincidental_source_rates_do_not_certify_future_projection():
    from app.demo import demo_evidence
    source = demo_evidence()[0]
    body = "In sample A, the rate was 30%. In a different sample B, it was 60%."
    source.passages = [EvidencePassage(paragraph_id="B000001", start=0, end=len(body), text=body, snapshot_hash="f"*64)]
    forecast = Forecast(status="scenario_only", conclusion="条件结论", supporting=[Claim(
        text="来源报告样本率30%，未来该领域将突破60%。", evidence_ids=[source.id])])
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(forecast, [source])


@pytest.mark.parametrize("text", [
    "并非假设参数：已经确认提升60%。",
    "不是假设比例：增长60%。",
    "非示意参数：增长60%。",
    "仅为假设参数示意：形式化率30%，而实际已确认普及率达到60%。",
    "仅为假设参数示意：形式化率30%而实际已确认普及率达到60%。",
])
def test_negated_or_mixed_hypothesis_labels_do_not_certify_factual_rates(text):
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(Forecast(status="scenario_only", conclusion=text), [])


@pytest.mark.parametrize("claim", [
    "来源报告，2027年底数学形式化率达到60%。",
    "来源报告，数学形式化率达到“60%”。",
    "来源报告，“在2025年，一项客户工作流的成本降低60%。”数学形式化率达到60%。",
    "来源报告该客户成本下降60%。",
])
def test_source_percentage_needs_an_attributed_complete_original_sentence(claim):
    from app.demo import demo_evidence
    source = demo_evidence()[0]
    body = "在2025年，一项客户工作流的成本降低60%。"
    source.passages = [EvidencePassage(paragraph_id="B000001", start=0, end=len(body), text=body, snapshot_hash="f"*64)]
    forecast = Forecast(status="scenario_only", conclusion="定性结论", supporting=[Claim(text=claim, evidence_ids=[source.id])])
    with pytest.raises(ValueError, match="定量比例"):
        validate_scenario_quantification(forecast, [source])
    forecast.supporting[0].text = f"来源报告：“{body}”"
    validate_scenario_quantification(forecast, [source])
