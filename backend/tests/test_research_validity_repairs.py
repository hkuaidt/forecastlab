from copy import deepcopy
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from app import graph
from app.agents.evidence import _claim_boundary_violations
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.llm import ModelCancelled
from app.resume import verify_saved_evidence_stage
from app.schemas import Forecast, QuestionSpec, Review, RunRecord, ScenarioForecast, WorldState
from app.storage import RunStore
from scenario_fixtures import scenario_details
from test_report_failure_recovery import saved_report


@pytest.mark.parametrize("category", ["AI", "LLM", "LLMs"])
def test_generic_model_category_does_not_require_literal_english_acronym(category):
    source = demo_evidence()[0]
    citation = SimpleNamespace(quote="Claude can design protein binders for the tested targets.", evidence_id=source.id, paragraph_id="B1")
    finding = SimpleNamespace(claim=f"Claude作为{category}模型可为所述目标设计蛋白结合体")
    assert _claim_boundary_violations(finding, [citation], {source.id:source}) == []


@pytest.mark.parametrize("extra", ["OpenAI", "GPT-9", "99%"])
def test_specific_entities_and_numbers_still_need_original_quote_support(extra):
    source = demo_evidence()[0]
    citation = SimpleNamespace(quote="Claude can design protein binders.", evidence_id=source.id, paragraph_id="B1")
    finding = SimpleNamespace(claim=f"Claude作为AI模型完成所述任务，{extra}")
    assert _claim_boundary_violations(finding, [citation], {source.id:source})


@pytest.mark.parametrize("has_findings", [True, False])
@pytest.mark.parametrize("damage", ["passage", "missing_snapshot"])
def test_current_resume_rechecks_original_even_with_trust_marker(tmp_path, monkeypatch, has_findings, damage):
    record = saved_report(tmp_path)
    if not has_findings:
        record.stage_outputs["evidence"]["evidence_assessment"]["findings"] = []
    source = record.stage_outputs["evidence"]["evidence"][0]
    if damage == "passage":
        source["passages"][0]["text"] = "缓存被改写的正文"
    else:
        source["snapshot_path"] = "sources-v1/" + "0"*32 + ".json"
    before = deepcopy(record.stage_outputs)
    monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: pytest.fail("No model before local source check"))
    graph.execute(record, record.evidence, RunStore(tmp_path), resume=True)
    assert record.status == "failed" and "核验失败" in record.errors[-1]
    assert record.stage_outputs == before


def test_integrity_check_does_not_rewrite_valid_upstream_record(tmp_path):
    record = saved_report(tmp_path)
    before = record.model_dump_json()
    verify_saved_evidence_stage(record, tmp_path)
    assert record.model_dump_json() == before


def test_scenario_policy_resolves_legacy_null_and_binary_defaults_only_in_private_input():
    question = QuestionSpec(question="项目未来会如何发展？", mode="scenario", resolution_rule="开放情景，概率为null。分析证明、实验和人才培养。")
    payload = {"question":question.model_dump(mode="json"), "evidence":[{"excerpt":"原文概率为null的示例不得改写"}]}
    before = deepcopy(payload)
    result = graph.scenario_forecast_context(payload)
    assert payload == before and result["evidence"] == before["evidence"]
    assert "null" not in result["question"]["resolution_rule"]
    assert "分析证明、实验和人才培养" in result["question"]["resolution_rule"]
    assert result["question"]["outcomes"] == []
    assert result["question"]["as_of"] == before["question"]["as_of"]
    assert result["forecast_policy"]["legacy_null_directive_replaced"]
    payload["question"]["outcomes"] = ["推进", "停滞"]
    assert graph.scenario_forecast_context(payload)["question"]["outcomes"] == ["推进", "停滞"]


def test_scenario_definitions_map_to_probabilities_and_valid_trace_ids():
    probabilities = {"推进":.6,"受限":.4}
    report = ScenarioForecast(conclusion="具体条件决定进展。", probabilities=probabilities, scenario_details=scenario_details(probabilities))
    question = QuestionSpec(question="项目未来会如何发展？", mode="scenario")
    graph.validate_forecast(report, question, demo_evidence(), WorldState(summary="状态"), [], Review(status="blocked"))
    assert report.status == "completed"
    report.scenario_details[0].name = "不对应的情景"
    with pytest.raises(ValueError, match="逐项对应"):
        graph.validate_forecast(report, question, demo_evidence(), WorldState(summary="状态"), [], Review(status="qualified"))
    report.scenario_details[0].name = "推进"
    report.scenario_details[0].evidence_ids = ["E999"]
    with pytest.raises(ValueError, match="情景说明证据"):
        graph.validate_forecast(report, question, demo_evidence(), WorldState(summary="状态"), [], Review(status="qualified"))
    assert Forecast.model_validate({"status":"completed","conclusion":"legacy","probabilities":probabilities}).scenario_details == []


def test_cancelled_forecast_never_falls_back_or_retries(tmp_path, monkeypatch):
    record = saved_report(tmp_path)
    event = Event()
    calls = []
    class CancelModel:
        def __init__(self, **kwargs):
            assert kwargs["cancel_event"] is event
            self.usage = kwargs["initial_usage"]
        def complete(self, *args, **kwargs):
            calls.append(args[0]); event.set()
            raise ModelCancelled("运行已取消")
    monkeypatch.setattr(graph, "ModelClient", CancelModel)
    graph.execute(record, record.evidence, RunStore(tmp_path), resume=True, cancel_event=event)
    assert calls == ["forecast"]
    assert record.status == "cancelled" and record.forecast is None
    assert "forecast" not in record.stage_outputs and record.finished_at is not None


def test_cancel_before_start_spends_no_model_call(tmp_path, monkeypatch):
    record = saved_report(tmp_path)
    event = Event(); event.set()
    monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: pytest.fail("cancelled before model construction"))
    graph.execute(record, record.evidence, RunStore(tmp_path), resume=True, cancel_event=event)
    assert record.status == "cancelled" and record.forecast is None


def test_four_relevant_actors_execute_concurrently_in_each_round(tmp_path):
    barrier = Barrier(4, timeout=3)
    actor_calls = []
    class FourActors:
        def complete(self, role, payload, schema, instructions):
            actor = payload.get("actor", {}).get("id")
            if role == "actor":
                actor_calls.append((payload["round"], actor))
                barrier.wait()
            output = demo_output(role, "A003" if actor=="A004" else actor, payload.get("round",1))
            if role == "world":
                fourth = deepcopy(output["actors"][-1])
                fourth.update(id="A004",name="独立验证社区",goal="复核验证结果")
                output["actors"].append(fourth)
            return schema.model_validate(output)
    record = RunRecord(run_id="four-actors", question=DEMO_QUESTION, evidence_mode="import", model="fixture")
    result = graph.build_graph(record, demo_evidence(), FourActors(), tmp_path).invoke({"question":DEMO_QUESTION.model_dump(mode="json")})
    assert len(result["world"]["actors"]) == 4 and len(result["actions"]) == 8
    assert {actor for _,actor in actor_calls} == {"A001","A002","A003","A004"}
    assert len(result["simulation"]) == 2


def test_cancelled_evidence_analysis_is_not_retried_or_replaced_by_gap(tmp_path):
    from app.agents.evidence import assess_evidence
    record = saved_report(tmp_path)
    calls = []
    class CancelModel:
        def complete(self, *args, **kwargs):
            calls.append(args[0])
            raise ModelCancelled("运行已取消")
    with pytest.raises(ModelCancelled):
        assess_evidence(record.question, None, record.retrieval_result, CancelModel(), tmp_path)
    assert calls == ["evidence12"]


def test_confirmed_legacy_scenario_rule_is_normalized_in_actual_forecast_input(tmp_path, monkeypatch, clear_framing):
    from app.schemas import QuestionFraming
    record = saved_report(tmp_path)
    record.question.resolution_rule = "开放情景，概率为null。保留实验与人才培养的分析。"
    clear_framing["proposed_spec"].update(mode="scenario", resolution_rule=record.question.resolution_rule)
    record.question_framing = QuestionFraming.model_validate(clear_framing)
    before = record.question_framing.model_dump_json()
    class Capture:
        def __init__(self, **kwargs):
            self.usage = kwargs["initial_usage"]
        def complete(self, role, payload, schema, instructions):
            assert role == "forecast"
            for spec in (payload["question"], payload["question_framing"]["proposed_spec"]):
                assert "null" not in spec["resolution_rule"]
                assert "实验与人才培养" in spec["resolution_rule"]
                assert spec.get("outcomes", []) == []
            probabilities = {"推进":.6,"受限":.4}
            return schema.model_validate({"conclusion":"若验证完成，后续可能推进。", "probabilities":probabilities,
                "scenario_details":scenario_details(probabilities)})
    monkeypatch.setattr(graph, "ModelClient", Capture)
    graph.execute(record, record.evidence, RunStore(tmp_path), resume=True)
    assert record.status == "completed", record.errors
    assert record.question_framing.model_dump_json() == before
    assert record.forecast_policy["version"] == "scenario-probability-v2"


def test_scenario_detail_wrapped_ids_use_existing_canonicalization():
    from app.schemas import SimulationStep
    world = WorldState.model_validate(demo_output("world"))
    simulation = [SimulationStep.model_validate(demo_output("environment"))]
    probabilities = {"推进":.6,"受限":.4}
    report = ScenarioForecast(conclusion="具体条件决定进展。", probabilities=probabilities, scenario_details=scenario_details(probabilities))
    detail = report.scenario_details[0]
    detail.evidence_ids = ["E001（测试记录）"]
    detail.assumption_ids = ["H001（阶段假设）"]
    detail.simulation_ids = ["S1（第一轮）"]
    graph.canonicalize_forecast_ids(report, demo_evidence(), world, simulation)
    assert (detail.evidence_ids, detail.assumption_ids, detail.simulation_ids) == (["E001"], ["H001"], ["S1"])
    graph.validate_forecast(report, QuestionSpec(question="项目未来会如何发展？", mode="scenario"),
        demo_evidence(), world, simulation, Review(status="passed"))
    detail.evidence_ids = ["E999（不存在）"]
    graph.canonicalize_forecast_ids(report, demo_evidence(), world, simulation)
    with pytest.raises(ValueError, match="情景说明证据引用不存在"):
        graph.validate_forecast(report, QuestionSpec(question="项目未来会如何发展？", mode="scenario"),
            demo_evidence(), world, simulation, Review(status="passed"))


@pytest.mark.parametrize("field,ref", [("assumption_ids","H001"), ("simulation_ids","S1")])
def test_evidence_only_report_checks_scenario_detail_dependencies(field, ref):
    from app.schemas import SimulationStep
    world = WorldState.model_validate(demo_output("world"))
    simulation = [SimulationStep.model_validate(demo_output("environment"))]
    probabilities = {"是":.6,"否":.4}
    report = Forecast(status="completed", conclusion="依据事前证据作主观分配。", probabilities=probabilities,
        scenario_details=scenario_details(probabilities))
    review = Review(status="passed", probability_basis="evidence_only")
    graph.validate_forecast(report, DEMO_QUESTION, demo_evidence(), world, simulation, review)
    setattr(report.scenario_details[0], field, [ref])
    with pytest.raises(ValueError, match="仅依据证据的情景说明"):
        graph.validate_forecast(report, DEMO_QUESTION, demo_evidence(), world, simulation, review)
