"""Create an auditable forecast-only child without mutating the failed parent."""
from hashlib import sha256
from uuid import uuid4

from . import config
from .provenance import load_snapshot, split_passages
from .resume import restore_legacy_evidence_stage
from .schemas import (ActorAction, Evidence, EvidenceAssessment, RunRecord,
                      SimulationStep, Review, WorldState, utcnow)

UPSTREAM_STAGES = ("question", "evidence", "world", "simulation", "review")


def is_legacy_report_failure(record: RunRecord) -> bool:
    forecast = record.forecast
    return bool(forecast and forecast.probabilities is None
                and forecast.conclusion.startswith("报告生成未通过结构校验")
                and any("校验未通过" in item for item in forecast.limitations))


def report_repair_info(record: RunRecord) -> dict:
    failure = (is_legacy_report_failure(record) or
               (record.status in {"failed", "partial", "interrupted"}
                and record.failed_stage == "forecast" and record.forecast is None)
               or (record.status == "partial" and record.forecast is not None
                   and record.forecast.probabilities is None and bool(record.evidence)))
    available = bool(failure and not record.demo and record.status not in {"queued", "running"}
                     and all(stage in record.stage_outputs for stage in UPSTREAM_STAGES))
    return {"available": available,
            "reason": "将重新核验原文，并新建只生成报告的修复运行；原记录和额度保留。" if available else None,
            "endpoint": f"/api/runs/{record.run_id}/repair-report" if available else None}


def prepare_report_repair(parent: RunRecord, data_dir) -> RunRecord:
    """Revalidate a private copy before any model call or child is persisted."""
    if not report_repair_info(parent)["available"]:
        raise ValueError("只有上游阶段完整的失败报告可以重新生成")
    child = parent.model_copy(deep=True)
    stage = child.stage_outputs["evidence"]
    assessment = stage.get("evidence_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("缺少保存的证据评估，不能仅重生成报告")
    if assessment.get("findings"):
        if assessment.get("findings_validated") is False:
            raise ValueError("保存的证据发现未经过原文校验，请重新导入原文创建运行")
        # Even previously trusted records must pass current source and claim checks.
        # Only this private copy loses the marker, never an explicit rejection.
        assessment.pop("findings_validated", None)
        restore_legacy_evidence_stage(child, data_dir)
    else:
        child.evidence = [Evidence.model_validate(item) for item in stage.get("evidence", [])]
        child.evidence_assessment = EvidenceAssessment.model_validate(assessment)
    # Cached source fields are model input too. Validate every supplied passage,
    # including assessments with no findings, and derive the excerpt from disk.
    for item in child.evidence:
        snapshot = load_snapshot(item, data_dir)
        original = {p.paragraph_id: p for p in split_passages(snapshot)}
        for passage in item.passages:
            if original.get(passage.paragraph_id) != passage:
                raise ValueError(f"{item.id} 保存的段落与原文不一致")
        item.excerpt = snapshot.text[:12000]
        item.content_hash = sha256(item.excerpt.encode()).hexdigest()
        if item.claim and item.claim not in snapshot.text:
            item.claim = "待依据已校验原文核查；旧概括未作为已核验证据。"
    child.stage_outputs["evidence"]["evidence"] = [item.model_dump(mode="json") for item in child.evidence]
    if child.retrieval_result is not None:
        child.retrieval_result.evidence = [item.model_copy(deep=True) for item in child.evidence]
    from .graph import check_ids, validated_finding_ids
    e_ids = {item.id for item in child.evidence}
    f_ids = validated_finding_ids(child.evidence_assessment, e_ids)
    child.world = WorldState.model_validate(child.stage_outputs["world"]["world"])
    child.actions = [ActorAction.model_validate(item) for item in child.stage_outputs["simulation"]["actions"]]
    child.simulation = [SimulationStep.model_validate(item) for item in child.stage_outputs["simulation"]["simulation"]]
    child.review = Review.model_validate(child.stage_outputs["review"]["review"])
    h_ids = {item.id for item in child.world.assumptions}
    a_ids = {item.id for item in child.world.actors}
    m_ids = {item.id for item in child.actions}
    s_ids = {item.id for item in child.simulation}
    for items in (child.world.assumptions, child.world.actors, child.actions, child.simulation):
        if len({item.id for item in items}) != len(items):
            raise ValueError("保存的因果轨迹存在重复编号")
    check_ids(child.world.evidence_refs, e_ids | f_ids, "世界状态")
    for actor in child.world.actors:
        check_ids(actor.visible_evidence_ids, e_ids | f_ids, "主体画像")
    for assumption in child.world.assumptions:
        check_ids(assumption.parent_ids, e_ids | f_ids | h_ids, "假设")
    for action in child.actions:
        check_ids([action.actor_id], a_ids, "主体行动")
        check_ids(action.parent_ids, s_ids | {"S0"}, "主体行动父节点")
    for step in child.simulation:
        check_ids(step.parent_ids, m_ids, "模拟父节点")
    for item in [*child.actions, *child.simulation]:
        check_ids(item.evidence_ids, e_ids, "因果轨迹证据")
        check_ids(item.assumption_ids, h_ids, "因果轨迹假设")
    for issue in child.review.issues:
        check_ids(issue.affected_ids, e_ids | f_ids | h_ids | a_ids | m_ids | s_ids, "审查意见")
    child.run_id = f"run_{uuid4().hex[:12]}"
    child.parent_run_id = parent.run_id
    child.report_repair_parent = parent.run_id
    child.report_repair_audit = {
        "parent_record_sha256": sha256(parent.model_dump_json().encode()).hexdigest(),
        "parent_status": parent.status, "parent_stage": parent.stage,
        "parent_usage": parent.usage.copy(), "parent_active_seconds": parent.active_seconds,
        "parent_errors": list(parent.errors), "new_call_limit": min(config.MAX_CALLS, 2),
        "revalidated_at": utcnow().isoformat(), "scope": "forecast_only",
    }
    child.stage_outputs = {stage: child.stage_outputs[stage] for stage in UPSTREAM_STAGES}
    child.forecast = None
    child.forecast_attempts = []
    child.shadow_forecast = None
    child.settlement = None
    child.status = "queued"
    child.stage = "forecast"
    child.failed_stage = None
    child.started_at = utcnow()
    child.finished_at = None
    child.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    child.active_seconds = 0
    child.preparation_records = []
    child.model_calls = []
    child.errors = []
    child.retry_history = []
    child.resume_count = 0
    child.stage_durations = {}
    child.model = config.MODEL_NAME
    return child
