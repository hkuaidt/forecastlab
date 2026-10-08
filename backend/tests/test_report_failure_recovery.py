"""Application validation failures remain failed and repair never trusts stale sources."""
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app import config, graph
from app.api import create_app
from app.agents.evidence import assess_evidence
from app.report_repair import prepare_report_repair, report_repair_info
from app.schemas import ImportedEvidence, QuestionSpec, RunRecord, Forecast
from app.sources import import_evidence
from app.storage import RunStore


class ReportModel:
    def __init__(self, outputs=(), **kwargs):
        self.outputs = iter(outputs)
        self.roles = []
        self.usage = dict(kwargs.get("initial_usage") or {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        self.kwargs = kwargs
        self.actual_model = "fixture"
        self.active_seconds = kwargs.get("initial_active_seconds", 0)

    def complete(self, role, payload, schema, instructions, **kwargs):
        self.roles.append(role)
        self.usage["calls"] += 1
        if role == "evidence12":
            source = payload["evidence"][0]; passage = source["passages"][0]
            return schema.model_validate({"summary": "原文核验", "findings": [{
                "claim": passage["text"], "relation": "background", "citations": [{
                    "evidence_id": source["id"], "snapshot_hash": source["snapshot_hash"],
                    "paragraph_id": passage["paragraph_id"], "quote": passage["text"]}]}]})
        assert role == "forecast", "Repair must not search, reassess, or rerun the world"
        return schema.model_validate(next(self.outputs))


def saved_report(tmp_path, *, legacy=False):
    question = QuestionSpec(question="后续发布如何演变？", mode="scenario")
    retrieval = import_evidence([ImportedEvidence(file_id="source", title="测试记录", body="测试已经完成。", excerpt="测试已经完成。")], question, tmp_path)
    assessment = assess_evidence(question, None, retrieval, ReportModel(), tmp_path)
    evidence = [item.model_dump(mode="json") for item in retrieval.evidence]
    record = RunRecord(run_id="failed_parent", question=question, model="fixture", evidence_mode="import",
        status="failed", stage="failed", failed_stage="forecast", errors=["报告内容或引用校验未通过"],
        evidence=retrieval.evidence, evidence_assessment=assessment, retrieval_result=retrieval,
        usage={"calls": 34, "prompt_tokens": 200000, "completion_tokens": 30000}, active_seconds=2700,
        stage_outputs={"question": {"question_analysis": {"normalized_question": question.question, "search_queries": []}},
            "evidence": {"evidence": evidence, "evidence_assessment": assessment.model_dump(mode="json")},
            "world": {"world": {"summary": "条件状态", "actors": [], "evidence_refs": ["E001"]}},
            "simulation": {"actions": [], "simulation": []}, "review": {"review": {"status": "passed"}}})
    if legacy:
        record.status = "scenario_only"; record.stage = "done"; record.failed_stage = None; record.errors = []
        record.forecast = Forecast(status="scenario_only", conclusion="报告生成未通过结构校验，已保存依据。",
            limitations=["自动报告的完整性校验未通过（情景中的定量比例）"])
        record.stage_outputs["forecast"] = {"forecast": record.forecast.model_dump(mode="json")}
        record.stage_outputs["evidence"]["evidence_assessment"].pop("findings_validated")
    return record


BAD = {"status": "scenario_only", "conclusion": "无依据比例", "scenarios": ["普及率达35%"]}
GOOD = {"status": "scenario_only", "conclusion": "若测试可复查，则可能推动后续发布。", "scenarios": ["若独立验证成功，则推进发布。"]}


def test_two_invalid_reports_fail_without_completed_checkpoint(tmp_path, monkeypatch):
    record = saved_report(tmp_path)
    record.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    model = ReportModel([BAD, BAD]); monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: model)
    store = RunStore(tmp_path)
    graph.execute(record, record.evidence, store, resume=True)
    saved = store.get(record.run_id)
    assert saved.status == "failed" and saved.stage == "failed" and saved.failed_stage == "forecast"
    assert saved.forecast is None and "forecast" not in saved.stage_outputs
    assert "定量比例" in saved.errors[-1]
    assert len(saved.forecast_attempts) == 2
    assert all(item.candidate.scenarios == BAD["scenarios"] and item.validation_errors for item in saved.forecast_attempts)
    assert model.roles == ["forecast", "forecast"]


def test_second_valid_candidate_keeps_rejected_candidate_audit(tmp_path, monkeypatch):
    record = saved_report(tmp_path)
    model = ReportModel([BAD, GOOD]); monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: model)
    graph.execute(record, record.evidence, RunStore(tmp_path), resume=True)
    assert record.status == "scenario_only" and record.stage == "done"
    assert record.forecast.conclusion == GOOD["conclusion"]
    assert record.forecast_attempts[0].validation_errors
    assert record.forecast_attempts[1].validation_errors == []


@pytest.mark.parametrize("legacy", [False, True])
def test_repair_endpoint_creates_bounded_child_and_preserves_parent(tmp_path, monkeypatch, legacy):
    record = saved_report(tmp_path, legacy=legacy)
    store = RunStore(tmp_path); store.save(record)
    parent_json = store.get(record.run_id).model_dump_json()
    snapshot = tmp_path / "runs" / record.run_id / (record.stage + ".json")
    original_snapshot = snapshot.read_bytes()
    models = []
    def factory(**kwargs):
        model = ReportModel([GOOD], **kwargs); models.append(model); return model
    monkeypatch.setattr(graph, "ModelClient", factory)
    monkeypatch.setattr(config, "MODEL_API_KEY", "fixture-key")
    with TestClient(create_app(tmp_path)) as client:
        info = client.get(f"/api/runs/{record.run_id}").json()["report_repair"]
        assert info["available"] and info["endpoint"].endswith("/repair-report")
        response = client.post(info["endpoint"])
        assert response.status_code == 202, response.text
        child = client.get(f"/api/runs/{response.json()['run_id']}").json()
        assert child["status"] == "scenario_only" and child["forecast"]["conclusion"] == GOOD["conclusion"]
        assert child["parent_run_id"] == record.run_id and child["report_repair_parent"] == record.run_id
        assert child["report_repair_audit"]["parent_usage"]["calls"] == 34
        assert child["usage"]["calls"] == 1 and not child["report_repair"]["available"]
    assert len(models) == 1 and models[0].roles == ["forecast"]
    assert models[0].kwargs["call_limit"] == 2
    assert models[0].kwargs["initial_usage"]["calls"] == 0
    assert store.get(record.run_id).model_dump_json() == parent_json
    assert snapshot.read_bytes() == original_snapshot


@pytest.mark.parametrize("damage", ["explicit_false", "quote", "number", "snapshot", "trace"])
def test_repair_gate_revalidates_even_previously_trusted_sources(tmp_path, monkeypatch, damage):
    record = saved_report(tmp_path)
    stage = record.stage_outputs["evidence"]
    finding = stage["evidence_assessment"]["findings"][0]
    if damage == "explicit_false": stage["evidence_assessment"]["findings_validated"] = False
    elif damage == "quote": finding["citations"][0]["quote"] = "原文中没有"
    elif damage == "number": finding["claim"] += "收入提升99%。"
    elif damage == "snapshot": (tmp_path / stage["evidence"][0]["snapshot_path"]).write_text("tampered")
    else: record.stage_outputs["world"]["world"]["evidence_refs"] = ["E999"]
    store = RunStore(tmp_path); store.save(record); original = store.get(record.run_id).model_dump_json()
    monkeypatch.setattr(config, "MODEL_API_KEY", "fixture-key")
    monkeypatch.setattr(graph, "ModelClient", lambda **kwargs: pytest.fail("No model before validation"))
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f"/api/runs/{record.run_id}/repair-report")
        assert response.status_code == 422, response.text
        assert "核验未通过" in response.json()["detail"]
        assert len(client.get("/api/runs").json()) == 1
    assert store.get(record.run_id).model_dump_json() == original


def test_successful_scenario_cannot_be_repaired_as_failure(tmp_path):
    record = saved_report(tmp_path)
    record.forecast = Forecast.model_validate(GOOD); record.status = "scenario_only"; record.stage = "done"
    assert not report_repair_info(record)["available"]
    with pytest.raises(ValueError): prepare_report_repair(record, tmp_path)


def test_repaired_partial_status_is_not_promoted_by_scenario_validation():
    from app.schemas import Review, WorldState
    question = QuestionSpec(question="该研究后续如何发展？", mode="scenario")
    report = graph.repair_forecast(Forecast.model_validate(BAD), question, [], WorldState(summary="无证据"), [], Review(status="passed"), "情景中的定量比例")
    assert report.status == "partial"


@pytest.mark.parametrize("findings", [True, False])
def test_report_repair_rejects_tampered_cached_passage_with_or_without_findings(tmp_path, findings):
    record = saved_report(tmp_path)
    stage = record.stage_outputs["evidence"]
    if not findings:
        stage["evidence_assessment"]["findings"] = []
    stage["evidence"][0]["passages"][0]["text"] = "发布审批已经通过，未解决问题为0。"
    before = deepcopy(record.model_dump(mode="json"))
    with pytest.raises(ValueError, match="段落与原文不一致"):
        prepare_report_repair(record, tmp_path)
    assert record.model_dump(mode="json") == before


@pytest.mark.parametrize("findings", [True, False])
def test_report_repair_uses_snapshot_text_instead_of_untrusted_cached_summary(tmp_path, findings):
    record = saved_report(tmp_path)
    stage = record.stage_outputs["evidence"]
    if not findings:
        stage["evidence_assessment"]["findings"] = []
    stage["evidence"][0]["excerpt"] = "审批通过99%"
    stage["evidence"][0]["claim"] = "监管机构已全部批准"
    child = prepare_report_repair(record, tmp_path)
    assert child.evidence[0].excerpt == "测试已经完成。"
    from hashlib import sha256
    assert child.evidence[0].content_hash == sha256(child.evidence[0].excerpt.encode()).hexdigest()
    assert "监管机构" not in child.evidence[0].claim
    assert child.stage_outputs["evidence"]["evidence"][0]["excerpt"] == "测试已经完成。"
    assert stage["evidence"][0]["excerpt"] == "审批通过99%"



def test_report_child_does_not_charge_parent_retrieval_time_again(tmp_path, monkeypatch):
    from app.schemas import RetrievalLog
    record = saved_report(tmp_path)
    record.retrieval_result.retrieval_log = [RetrievalLog(task_id="R001", query="old query", status="success", elapsed_seconds=config.MAX_SECONDS + 10)]
    child = prepare_report_repair(record, tmp_path)
    models = []
    def factory(**kwargs):
        model = ReportModel([GOOD], **kwargs); models.append(model); return model
    monkeypatch.setattr(graph, "ModelClient", factory)
    graph.execute(child, child.evidence, RunStore(tmp_path), resume=True)
    assert child.status == "scenario_only"
    assert models[0].kwargs["initial_active_seconds"] == 0
    assert models[0].roles == ["forecast"]
    assert child.retrieval_result.retrieval_log[0].elapsed_seconds > config.MAX_SECONDS


def test_report_child_resume_keeps_durable_ledger_usage_after_crash(tmp_path, monkeypatch):
    child = prepare_report_repair(saved_report(tmp_path), tmp_path)
    store = RunStore(tmp_path)
    call = store.reserve_call(child.run_id, "runtime", call_limit=2, input_hash="old-attempt", prompt_version="test")
    call.status = "succeeded"; call.prompt_tokens = 101; call.completion_tokens = 21; call.usage_known = True; call.elapsed_seconds = 12
    store.finish_call(call)
    child.status = "interrupted"
    models = []
    def factory(**kwargs):
        model = ReportModel([GOOD], **kwargs); models.append(model); return model
    monkeypatch.setattr(graph, "ModelClient", factory)
    graph.execute(child, child.evidence, store, resume=True)
    assert child.status == "scenario_only"
    assert models[0].kwargs["initial_usage"] == {"calls": 1, "prompt_tokens": 101, "completion_tokens": 21}
    assert child.usage == {"calls": 2, "prompt_tokens": 101, "completion_tokens": 21}
    assert models[0].kwargs["initial_active_seconds"] == 12
