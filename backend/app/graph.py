"""Explicit SAO workflow. Each node owns a single stage output."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from math import ceil, isfinite
import re
import time
from typing import TypedDict
from uuid import uuid4
from langgraph.graph import StateGraph, START, END
from .schemas import (QuestionSpec, QuestionAnalysis, Evidence, EvidenceAssessment, EvidenceOnlyAudit,
                      WorldState, ActorAction, SimulationStep, Review, ReviewIssue, Forecast,
                      EvidenceOnlyForecast, ScenarioForecast, ForecastAttempt, RunRecord, utcnow)
from .sources import online_search, retrieve_evidence
from .schemas import RetrievalResult, RetrievalLog, RetrievalTask, Assumption
from .agents.evidence import (assess_evidence, active_framing, make_evidence_context, EvidenceStageError,
                              verified_coverage_summary)
from .llm import unique_request_count, request_active_seconds
from .llm import BudgetExceeded, ModelClient, ModelCancelled
from .demo import demo_output
from . import config
from .cutoff_gaps import is_future_outcome_gap
from .resume import restore_legacy_evidence_stage, verify_saved_evidence_stage
from .forecast_wire import DefinitionFirstForecast, WIRE_FORMAT, WIRE_INSTRUCTIONS, definition_first_task, to_public_forecast, review_first_view, qualify_model_claims


class FlowState(TypedDict, total=False):
    question: dict
    question_analysis: dict
    question_framing: dict
    premise_assumption_map: dict
    evidence: list[dict]
    evidence_assessment: dict
    world: dict
    actions: list[dict]
    simulation: list[dict]
    review: dict
    forecast: dict


def evidence_for_model(items: list[Evidence], limit: int = 2400) -> list[dict]:
    """Keep model context bounded while retaining complete evidence in snapshots."""
    return [{**e.model_dump(mode="json", exclude={"snapshot_path", "content_hash"}),
             "excerpt": e.excerpt[:limit]} for e in items]


def available_at_cutoff(evidence: Evidence, as_of) -> bool:
    """Allow server-validated frozen evidence or dated exercise material at the cutoff."""
    if evidence.availability in {"verified_before_cutoff", "live_near_cutoff"}:
        return True
    if evidence.retrieved_at <= as_of:
        return True
    return (evidence.source_type == "exercise" and evidence.published_at is not None
            and evidence.published_at <= as_of
            and (evidence.updated_at is None or evidence.updated_at <= as_of)
            and (evidence.event_at is None or evidence.event_at <= as_of))


def mistakes_future_outcome_for_missing_evidence(text: str, question: QuestionSpec,
                                                 *, assume_missing: bool = False) -> bool:
    return is_future_outcome_gap(text, question, assume_missing=assume_missing)


def nonblocking_audit_reason(reason: str, question: QuestionSpec, evidence: list[Evidence]) -> bool:
    """Drop only reasons that violate forecast-time semantics, never substantive pre-cutoff gaps."""
    if mistakes_future_outcome_for_missing_evidence(reason, question, assume_missing=True):
        return True
    cutoff_verified = bool(evidence) and all(e.availability == "verified_before_cutoff" for e in evidence)
    if cutoff_verified and re.search(r"(?:证据|资料).*(?:仅|只).*?(?:覆盖|包含).*?(?:截点|截至日|信息截点).*?(?:及以前|及之前|之前|当日)", reason):
        if not re.search(r"缺少|缺失|不足|无法|不能|没有|未提供|不支持|无关|尚未|未知|不具备", reason):
            return True
    if cutoff_verified and re.search(
        r"历史练习|事后整理|非(?:当时)?冻结|盲回测|回看偏差|source_type\s*=\s*exercise|date_status\s*(?:为|=)\s*unknown",
        reason, re.I,
    ):
        substantive_quality = re.search(
            r"缺少|缺失|不足|没有|未提供|不支持|无关|尚未|未知|无法|不能|不具备|"
            r"次级来源|非\s*(?:LBMA|官方|权威)|来源可靠性|来源质量|口径|交叉校验|无法核查|内容不支持|与目标.*(?:无关|弱相关)",
            reason, re.I,
        )
        return substantive_quality is None
    return False


def sanitize_evidence_audit(audit: EvidenceOnlyAudit, question: QuestionSpec,
                            evidence: list[Evidence]) -> tuple[bool, list[str], list[str]]:
    """Return effective can_estimate, blocking reasons, and discarded non-blocking reasons."""
    raw = list(audit.blocking_reasons)
    discarded = [reason for reason in raw if nonblocking_audit_reason(reason, question, evidence)]
    effective = [reason for reason in raw if reason not in discarded]
    can_estimate = audit.can_estimate
    if not can_estimate and raw and not effective:
        can_estimate = True
    return can_estimate, effective, discarded


def market_price_context(question: QuestionSpec, evidence: list[Evidence]) -> dict | None:
    """Describe dated price coverage without treating recent momentum as a base rate."""
    if question.mode != "binary" or question.resolve_by is None or not re.search(
        r"指数|股价|股市|股票|A股|ETF|收盘点位|收盘价|期货|汇率", question.question, re.I
    ):
        return None
    horizon_days = max(1, ceil((question.resolve_by - question.as_of).total_seconds() / 86400))
    price_items = [item for item in evidence if re.search(
        r"收盘|收于|涨幅|跌幅|日涨|成交额|振幅|点位|盘中最高|盘中最低", item.excerpt
    ) and item.event_at is not None and item.event_at <= question.as_of]
    return {
        "forecast_horizon_days": horizon_days,
        "dated_price_observation_days": len({item.event_at.date() for item in price_items}),
        "price_source_groups": len({item.source_group for item in price_items}),
        "note": "这些是证据包中明确标注事件日的价格资料数量，不代表完整历史价格序列或经验基准率。",
    }


def trace_for_model(state: FlowState) -> dict:
    """Keep the causal trace and references without repeating verbose agent prose."""
    action_keys = {"id", "actor_id", "round", "action", "evidence_ids", "assumption_ids", "conditions", "parent_ids", "parent_state"}
    step_keys = {"id", "round", "summary", "state_changes", "conflicts", "unresolved", "evidence_ids", "assumption_ids", "parent_ids", "parent_state", "next_state"}
    return {
        "actions": [{key: value for key, value in action.items() if key in action_keys} for action in state["actions"]],
        "simulation": [{key: value for key, value in step.items() if key in step_keys} for step in state["simulation"]],
    }


def check_ids(ids: list[str], valid: set[str], label: str):
    missing = set(ids) - valid
    if missing:
        raise ValueError(f"{label}引用不存在：{', '.join(sorted(missing))}")


def canonical_ids(values: list[str], valid: set[str]) -> list[str]:
    """Accept an ID wrapped in explanatory prose, never create a new reference."""
    normalized = []
    for value in values:
        matches = ([value] if value in valid else sorted(
            (item for item in valid if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(item)}(?![A-Za-z0-9_-])", value)),
            key=value.find))
        normalized.extend(matches or [value])
    return list(dict.fromkeys(normalized))


def finding_evidence_map(assessment: dict | None) -> dict[str, list[str]]:
    """Map finding ids (``F001``…) to the evidence ids they cite.

    Review/世界状态 stages routinely reference a finding when they mean the
    evidence behind it. Without this map those references fail validation and the
    whole run aborts, which is what used to happen on more than half of cases.
    """
    if not assessment or not assessment.get("findings_validated", False):
        # Legacy/model-supplied findings are not certified quote references.
        return {}
    mapping: dict[str, list[str]] = {}
    for finding in (assessment or {}).get("findings") or []:
        fid = finding.get("id")
        ids = [c.get("evidence_id") for c in (finding.get("citations") or []) if c.get("evidence_id")]
        if fid and ids:
            mapping[fid] = list(dict.fromkeys(ids))
    return mapping


def resolve_refs(values: list[str], valid: set[str],
                 *, expansions: dict[str, list[str]] | None = None) -> tuple[list[str], list[str]]:
    """Canonicalize model-authored references, expand aliases, drop the rest.

    Model references are advisory: each resolved id is kept, an alias (a finding
    id) is expanded to the ids it stands for, and anything still unknown is
    dropped and reported rather than raising — the surviving output still
    satisfies "no invented ids", and one bad id no longer aborts the run.
    """
    aliases = expansions or {}
    allowed = valid | set(aliases)
    resolved: list[str] = []
    dropped: list[str] = []
    for value in values:
        hits: list[str] = []
        for item in canonical_ids([value], allowed):
            if item in valid:
                hits.append(item)
            else:
                hits.extend(i for i in aliases.get(item, ()) if i in valid)
        if hits:
            resolved.extend(hits)
        else:
            dropped.append(value)
    return list(dict.fromkeys(resolved)), dropped


def sanitize_review_issue_ids(issue: ReviewIssue, valid: set[str]) -> list[str]:
    """Keep review prose but drop locator IDs that are not valid trace nodes.

    Review affected_ids are navigation hints, not evidence themselves. An invalid
    locator must never become a trusted reference, but it also should not abort an
    otherwise auditable run. Dropped IDs are recorded in the explanation.
    """
    normalized = canonical_ids(issue.affected_ids, valid)
    invalid = [item for item in normalized if item not in valid]
    issue.affected_ids = [item for item in normalized if item in valid]
    if invalid:
        suffix = f"系统已忽略无效定位编号：{'、'.join(invalid)}。"
        issue.explanation = (issue.explanation.rstrip("。") + "；" + suffix) if issue.explanation else suffix
    return invalid


def canonicalize_world_ids(world: WorldState) -> tuple[dict[str, str], dict[str, str]]:
    """Assign server-owned namespaces to model-generated actors and assumptions."""
    old_actor_ids = [actor.id for actor in world.actors]
    old_assumption_ids = [assumption.id for assumption in world.assumptions]
    if len(set(old_actor_ids)) != len(old_actor_ids):
        raise ValueError("主体 ID 重复")
    if len(set(old_assumption_ids)) != len(old_assumption_ids):
        raise ValueError("假设 ID 重复")
    actor_map = {old: f"A{i:03}" for i, old in enumerate(old_actor_ids, 1)}
    assumption_map = {old: f"H{i:03}" for i, old in enumerate(old_assumption_ids, 1)}
    for actor in world.actors:
        actor.id = actor_map[actor.id]
    for assumption in world.assumptions:
        old = assumption.id
        assumption.id = assumption_map[old]
        assumption.parent_ids = [assumption_map.get(parent, parent) for parent in assumption.parent_ids]
    return actor_map, assumption_map


def validated_finding_ids(assessment: EvidenceAssessment, evidence_ids: set[str]) -> set[str]:
    """Return only server-validated F IDs whose citations trace to current E evidence."""
    if assessment.findings and not assessment.findings_validated:
        raise ValueError("证据发现未经过原文校验")
    finding_ids = [finding.id for finding in assessment.findings]
    if len(set(finding_ids)) != len(finding_ids):
        raise ValueError("证据发现 ID 重复")
    for finding in assessment.findings:
        check_ids([citation.evidence_id for citation in finding.citations], evidence_ids, "证据发现")
    return set(finding_ids)


def canonicalize_forecast_ids(forecast: Forecast, evidence: list[Evidence], world: WorldState,
                              simulation: list[SimulationStep],
                              assessment: dict | None = None) -> None:
    evidence_ids = {item.id for item in evidence}
    assumption_ids = {item.id for item in world.assumptions}
    simulation_ids = {item.id for item in simulation}
    aliases = {}
    if assessment and assessment.get("findings_validated") is True:
        checked = EvidenceAssessment.model_validate(assessment)
        trusted = validated_finding_ids(checked, evidence_ids)
        aliases = {fid: ids for fid, ids in finding_evidence_map(assessment).items() if fid in trusted}
    for claim in [*forecast.supporting, *forecast.opposing, *forecast.scenario_details]:
        refs = canonical_ids(claim.evidence_ids, evidence_ids | set(aliases))
        # F is an already validated intermediary, not another external source.
        # Unknown/unvalidated F stays in place for normal rejection; never guess E.
        claim.evidence_ids = list(dict.fromkeys(eid for ref in refs for eid in aliases.get(ref, [ref])))
        claim.assumption_ids = canonical_ids(claim.assumption_ids, assumption_ids)
        claim.simulation_ids = canonical_ids(claim.simulation_ids, simulation_ids)
        # A literal reference in public prose is an auditable locator. Fill only
        # the real S IDs actually mentioned here; never attach the whole trace.
        reference_text = " ".join([getattr(claim, "text", ""), getattr(claim, "definition", ""),
                                   getattr(claim, "rationale", ""), *getattr(claim, "conditions", [])])
        mentioned = [step.id for step in simulation if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(step.id)}(?![A-Za-z0-9_])", reference_text)]
        claim.simulation_ids = list(dict.fromkeys([*claim.simulation_ids, *mentioned]))
    forecast.key_assumptions = canonical_ids(forecast.key_assumptions, assumption_ids)


_REPORT_PERCENTAGE = re.compile(r"\d+(?:\.\d+)?\s*[%％]|百分之[零〇一二三四五六七八九十百点两\d.]+|\d+(?:\.\d+)?\s*个?百分点")
_EXPLICIT_HYPOTHETICAL_RATE = re.compile(
    r"(?:仅为|纯属|仅是).{0,4}假设|假设(?:数值|参数|比例)|^假设[：:]|"
    r"示意|非统计|未经校准|无数据(?:依据|支撑)|没有数据(?:支持|支撑)|未经实测"
)


_MASKED_MODEL_RATE = "[未校准的模型比例已省略；仅作定性条件推演]"
_MODEL_DERIVED_RATE = re.compile(
    r"\d+(?:\.\d+)?\s*[%％]?\s*(?:[-–—~～]|至|到)\s*\d+(?:\.\d+)?\s*[%％]|"
    + _REPORT_PERCENTAGE.pattern
)


def scenario_forecast_context(payload: dict) -> dict:
    """Mask derived rates in a private model input, never source/audit records."""
    safe = deepcopy(payload)
    question = safe.get("question", {})
    if question.get("mode") == "scenario":
        old_rule = question.get("resolution_rule") or ""
        question["resolution_rule"] = re.sub(
            r"(?:概率|probabilities?)\s*(?:必须为|应为|为|是|[:=])\s*null\b|不输出概率",
            "输出未经校准的场景主观概率", old_rule, flags=re.I)
        old_outcomes = question.get("outcomes", [])
        default_binary = {str(item).strip().casefold() for item in old_outcomes} in ({"是", "否"}, {"yes", "no"})
        if default_binary:
            question["outcomes"] = []
        safe["forecast_policy"] = {
            "version": "scenario-probability-v2", "mode": "scenario",
            "probabilities": "subjective_uncalibrated", "scenario_details_required": True,
            "legacy_null_directive_replaced": question["resolution_rule"] != old_rule,
            "legacy_binary_outcomes_ignored": default_binary,
        }

    def mask(value, key=""):
        # Trace identities and source/time metadata must remain stable.
        if (key == "id" or key.endswith("_id") or key.endswith("_ids")
                or key.endswith("_at") or key in {"created_by", "date", "as_of"}):
            return value
        if isinstance(value, str):
            return _MODEL_DERIVED_RATE.sub(_MASKED_MODEL_RATE, value)
        if isinstance(value, dict):
            return {name: mask(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [mask(item) for item in value]
        return value

    for key in ("world", "actions", "simulation", "review"):
        if key in safe:
            safe[key] = mask(safe[key])
    safe["forecast_context_provenance"] = {
        "source_material": "evidence及evidence_assessment中的原文和引用保持不变；原文匹配不等于独立事实认证，须保留来源及日期限制。",
        "model_derived": "world/actions/simulation/review是模型构造的假设、模拟与审查判断，不是新增观测；其中未校准比例已遮蔽，不得补造。",
        "report_rule": "区分来源与模型推演，给出未校准的场景主观概率；不要把E/H/S混合引用当实测认证。",
    }
    return safe


def validate_forecast_claim_kinds(forecast: Forecast) -> None:
    invalid = []
    conditional = re.compile(
        r"^(?:若|如果|假如|假设|仅当|当.+?时|仅在.+?时|"
        r"(?:模型)?(?:假设|模拟|情景|推演)(?:[：:]|中|结果|表明|显示|认为)|"
        r"(?:根据|基于).{0,40}(?:模拟|假设|推演)|在.{0,40}(?:假设|条件|模拟|情景).{0,8}(?:下|中)|"
        r"if\b|assuming\b|under (?:the |this )?(?:assumption|scenario)|in (?:this |the )?simulation|scenario:)", re.I)
    for name in ("supporting", "opposing"):
        for index, claim in enumerate(getattr(forecast, name)):
            if not (claim.assumption_ids or claim.simulation_ids):
                continue
            factual_assertion = re.search(r"实际上|实际已|已经?确认|已(?:被)?证实|事实(?:上|是)|"
                                          r"观测(?:显示|证明)|实测(?:显示|证实)", claim.text)
            if not conditional.search(claim.text.strip()) or factual_assertion:
                invalid.append(f"{name}[{index}]")
    if invalid:
        raise ValueError("引用H/S的报告主张必须明确写成条件、假设或模拟；需修改字段：" + ", ".join(invalid)
                         + "。可用‘若…则可能…’或‘模型假设：…’；E/H/S混合引用不能认证无条件事实。")


def _source_statistic_quote_spans(text: str, claim, evidence: list[Evidence]) -> list[tuple[int, int]]:
    """Only attributed, verbatim source sentences can certify report percentages."""
    if not (claim and claim.evidence_ids and not (claim.assumption_ids or claim.simulation_ids)):
        return []
    if not re.search(r"(?:原文|来源|作者|厂商|客户).{0,12}(?:报告|称|表示|反馈|记录)", text):
        return []
    # A bare matching number (or translated paraphrase) cannot establish subject,
    # task or time scope. Require the complete rate-bearing source sentence.
    def sentence_text(value):
        return value.strip().rstrip("。！？!?；;.").strip()
    source_sentences = {
        sentence_text(sentence)
        for source in evidence if source.id in claim.evidence_ids
        for passage in source.passages
        for sentence in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+", passage.text)
        if _REPORT_PERCENTAGE.search(sentence)
    }
    spans = []
    for match in re.finditer(r'“([^”]+)”|"([^"\n]+)"|「([^」]+)」', text):
        quoted = next(group for group in match.groups() if group is not None)
        if _REPORT_PERCENTAGE.search(quoted) and sentence_text(quoted) in source_sentences:
            spans.append(match.span())
    return spans


def validate_scenario_quantification(forecast: Forecast, evidence: list[Evidence]) -> None:
    """A simulated rate cannot become a fact by moving to another report field."""
    fields = [("conclusion", forecast.conclusion, None)]
    for name in ("supporting", "opposing"):
        fields.extend((f"{name}[{index}]", claim.text, claim) for index, claim in enumerate(getattr(forecast, name)))
    for name in ("scenarios", "new_information", "limitations", "key_assumptions"):
        fields.extend((f"{name}[{index}]", text, None) for index, text in enumerate(getattr(forecast, name)))
    invalid_fields = []
    for field, text, claim in fields:
        source_quotes = _source_statistic_quote_spans(text, claim, evidence)
        # Disclaimers apply to the local clause only, not adjacent factual claims.
        start = 0
        boundaries = list(re.finditer(r"[。！？!?；;，,\n]|(?<=\.)\s+", text))
        clauses = []
        for boundary in boundaries:
            clauses.append((start, text[start:boundary.start()]))
            start = boundary.end()
        clauses.append((start, text[start:]))
        for offset, clause in clauses:
            explicit_hypothesis = bool(_EXPLICIT_HYPOTHETICAL_RATE.search(clause.strip()))
            negated_label = re.search(r"(?:并非|并不是|不是|并不|不属于|不应称为|不能称为|非(?=假设|示意)).{0,8}"
                                      r"(?:假设|示意|非统计|未经校准|无数据|没有数据|未经实测)", clause)
            factual_assertion = re.search(r"实际|已经?确认|实测(?:结果|显示)|已测得|已证实|已验证", clause)
            for rate in _REPORT_PERCENTAGE.finditer(clause):
                if explicit_hypothesis and not (negated_label or factual_assertion):
                    continue
                if any(left <= offset + rate.start() and offset + rate.end() <= right for left, right in source_quotes):
                    continue
                if field not in invalid_fields:
                    invalid_fields.append(field)
    if invalid_fields:
        raise ValueError("场景报告中的定量比例缺少明确的假设/示意标注或直接来源依据；需修改字段："
                         + ", ".join(invalid_fields) + "。模拟及H假设不证明比例，请改为定性条件，不能把它移入其他报告字段")



def validate_scenario_end_states(forecast: Forecast | DefinitionFirstForecast, simulation: list[SimulationStep]) -> None:
    """Reject literal round identifiers or copied states, not semantic outcome labels."""
    step_ids = {step.id for step in simulation}
    normalize = lambda text: " ".join(text.split())
    summaries = {normalize(step.summary) for step in simulation if step.summary.strip()}
    invalid = []
    private = isinstance(forecast, DefinitionFirstForecast)
    details = forecast.terminal_definitions if private else forecast.scenario_details
    field = "terminal_definitions" if private else "scenario_details"
    for name in ([] if private else forecast.probabilities or {}):
        if name.strip() in step_ids:
            invalid.append(f"probabilities[{name}]")
    for index, detail in enumerate(details):
        if detail.name.strip() in step_ids:
            invalid.append(f"{field}[{index}].name")
        if normalize(detail.definition) in summaries:
            invalid.append(f"{field}[{index}].definition")
    if invalid:
        raise ValueError("S编号和逐字轮次摘要表示同一轨迹的先后状态，不能充当互斥终局；需修改字段："
                         + ", ".join(invalid)
                         + "。请在同一目标日期、范围和判定轴下给出具名终态；S仅作为实际使用的模拟依据引用。")


def validate_forecast(forecast: Forecast, question: QuestionSpec, evidence: list[Evidence], world: WorldState,
                      simulation: list[SimulationStep], review: Review, *, require_probability: bool = False):
    evidence_ids = {e.id for e in evidence}
    assumption_ids = {a.id for a in world.assumptions}
    simulation_ids = {s.id for s in simulation}
    evidence_only = question.mode == "binary" and review.probability_basis == "evidence_only"
    for claim in forecast.supporting + forecast.opposing:
        check_ids(claim.evidence_ids, evidence_ids, "报告证据")
        check_ids(claim.assumption_ids, assumption_ids, "报告假设")
        check_ids(claim.simulation_ids, simulation_ids, "报告模拟")
        if not (claim.evidence_ids or claim.assumption_ids or claim.simulation_ids):
            raise ValueError("报告主张没有可展开的依据")
        if evidence_only and (claim.assumption_ids or claim.simulation_ids):
            raise ValueError("仅依据证据的概率不能引用假设或模拟")
    for detail in forecast.scenario_details:
        check_ids(detail.evidence_ids, evidence_ids, "情景说明证据")
        check_ids(detail.assumption_ids, assumption_ids, "情景说明假设")
        check_ids(detail.simulation_ids, simulation_ids, "情景说明模拟")
        if not (detail.evidence_ids or detail.assumption_ids or detail.simulation_ids):
            raise ValueError(f"情景 {detail.name} 缺少可展开的E/H/S依据")
        if evidence_only and (detail.assumption_ids or detail.simulation_ids):
            raise ValueError("仅依据证据的情景说明不能引用假设或模拟")
    check_ids(forecast.key_assumptions, assumption_ids, "关键假设")
    if evidence_only and forecast.key_assumptions:
        raise ValueError("仅依据证据的概率不能依赖建模假设")
    if not evidence:
        forecast.probabilities = None
        if forecast.status != "partial":
            forecast.status = "scenario_only" if question.mode == "scenario" else "insufficient_evidence"
    semantic_warnings = []
    try:
        validate_forecast_claim_kinds(forecast)
    except ValueError as exc:
        semantic_warnings.append(str(exc))
    if question.mode == "scenario":
        try:
            validate_scenario_quantification(forecast, evidence)
        except ValueError as exc:
            semantic_warnings.append(str(exc))
        if any(re.match(r"^E\d+\s*(?:显示|指出|提及)", item) for item in forecast.new_information):
            semantic_warnings.append("后续信息可能复述来源判断，请结合原文核验。")
    if review.status == "blocked":
        semantic_warnings.append("审查仍有未解决问题；结论和概率是有保留的模型判断。")
    for warning in semantic_warnings:
        note = "质量提示（不阻断报告）：" + warning
        if note not in forecast.limitations:
            forecast.limitations.append(note)
    if require_probability and evidence and forecast.probabilities is None:
        raise ValueError("已有证据，请给出未经校准的主观概率；probabilities不能为null")
    if forecast.probabilities is not None:
        if question.mode == "scenario":
            if len(forecast.probabilities) < 2 or any(not key.strip() for key in forecast.probabilities):
                raise ValueError("场景概率需至少两个有名称的情景")
        elif set(forecast.probabilities) != set(question.outcomes):
            raise ValueError("概率结果选项与问题不一致")
        if (any(not isfinite(p) or p < 0 or p > 1 for p in forecast.probabilities.values())
                or abs(sum(forecast.probabilities.values()) - 1) > .001):
            raise ValueError("概率须在 0–1 且合计为 1")
        if question.mode == "scenario" and forecast.scenario_details:
            names = [detail.name for detail in forecast.scenario_details]
            if len(set(names)) != len(names) or set(names) != set(forecast.probabilities):
                raise ValueError("scenario_details名称必须与probabilities逐项对应且不重复")
        if question.mode == "scenario":
            validate_scenario_end_states(forecast, simulation)
        forecast.status = "completed"
        note = "概率为模型基于现有证据与假设给出的主观分配，未经校准，不是来源实测频率。"
        if note not in forecast.limitations:
            forecast.limitations.append(note)
    elif forecast.status != "partial":
        forecast.status = "scenario_only" if question.mode == "scenario" else "insufficient_evidence"
    if evidence_only and forecast.probabilities is not None:
        forecast.probability_basis = "evidence_only"



def repair_forecast(forecast: Forecast, question: QuestionSpec, evidence: list[Evidence], world: WorldState,
                    simulation: list[SimulationStep], review: Review, reason: str) -> Forecast:
    """Conservatively retain only traceable claims after model correction fails."""
    evidence_ids = {item.id for item in evidence}
    assumption_ids = {item.id for item in world.assumptions}
    simulation_ids = {item.id for item in simulation}
    removed = 0
    for name in ("supporting", "opposing"):
        valid_claims = []
        for claim in getattr(forecast, name):
            claim.evidence_ids = [item for item in claim.evidence_ids if item in evidence_ids]
            claim.assumption_ids = [item for item in claim.assumption_ids if item in assumption_ids]
            claim.simulation_ids = [item for item in claim.simulation_ids if item in simulation_ids]
            if claim.evidence_ids or claim.assumption_ids or claim.simulation_ids:
                valid_claims.append(claim)
            else:
                removed += 1
        setattr(forecast, name, valid_claims)
    forecast.key_assumptions = [item for item in forecast.key_assumptions if item in assumption_ids]
    if question.mode == "scenario":
        forecast.scenarios = [item for item in forecast.scenarios
                              if not re.search(r"\d+(?:\.\d+)?\s*[%％]", item)
                              or re.search(r"假设|示意|非统计|未经校准", item)]
        forecast.new_information = [item for item in forecast.new_information
                                    if not re.match(r"^E\d+\s*(?:显示|指出|提及)", item)]
    if any(marker in reason for marker in ("定量比例", "后续信息应描述")):
        # Field-level unsupported assertion: retain only audit trail.
        forecast.supporting = []
        forecast.opposing = []
        forecast.scenarios = []
        forecast.new_information = []
        forecast.key_assumptions = []
        forecast.limitations = [item for item in forecast.limitations if "校验未通过" in item]
    forecast.probabilities = None
    forecast.status = "partial"
    forecast.conclusion = "报告内容或引用校验未通过；候选输出和具体原因已保留，尚未形成有效报告。"
    forecast.limitations.append(f"报告内容或引用校验未通过：{reason[:500]}")
    if removed:
        forecast.limitations.append(f"已移除 {removed} 条无法追溯的主张。")
    validate_forecast(forecast, question, evidence, world, simulation, review)
    return forecast


STAGE_NODES = (
    ("question", "define_question"), ("evidence", "retrieve"), ("world", "model_world"),
    ("simulation", "simulate"), ("review", "audit"), ("forecast", "synthesize"),
)


def build_graph(record: RunRecord, imported: list[Evidence], model: ModelClient | None, data_dir, *, start_at: str = "define_question", store=None, cancel_event=None):
    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("运行已取消")
    def ask(role, payload, schema, instructions, *, actor_id=None, round_number=1):
        check_cancelled()
        if record.demo:
            return schema.model_validate(demo_output(role, actor_id, round_number))
        if record.question_framing:
            payload = dict(payload)
            is_evidence_only = role == "evidence_audit" or (role == "forecast" and payload.get("valid_assumption_ids") == [] and "world" not in payload)
            frame_context = active_framing(record.question_framing)
            if not is_evidence_only:
                payload["question_framing"] = frame_context
            else:
                payload["clarification_answers"] = frame_context["clarification_answers"]
            key = "evidence" if "evidence" in payload else "visible_evidence" if "visible_evidence" in payload else None
            if key and record.evidence_assessment:
                ids = {e["id"] for e in payload[key]}
                selected = [e for e in record.evidence if e.id in ids]
                budget = 1600 if role == "world" else 900 if role == "forecast" else 1200
                context = make_evidence_context(selected, record.evidence_assessment, max_chars_per_source=budget)
                payload[key] = context["evidence"]
                payload["evidence_assessment"] = context["assessment"].model_dump(mode="json")
                payload["evidence_context_limitations"] = context["limitations"]
            if role in {"world", "actor", "environment", "review", "forecast"}:
                # Downstream references must use E/H/M/S, while the persisted
                # framing keeps full premise IDs for provenance and user review.
                if "question_framing" in payload:
                    frame = payload["question_framing"]
                    payload["question_framing"] = {
                        "revision": frame["revision"], "proposed_spec": frame["proposed_spec"],
                        "clarification_answers": frame["clarification_answers"],
                        "premises": [{key: premise[key] for key in ("content", "user_review", "treatment")}
                                     for premise in frame["premises"]],
                    }
                if "evidence_assessment" in payload:
                    def remove_premise_targets(value):
                        if isinstance(value, dict):
                            return {key: remove_premise_targets(item) for key, item in value.items()
                                    if key != "target_premise_ids"}
                        if isinstance(value, list):
                            return [remove_premise_targets(item) for item in value]
                        return value
                    payload["evidence_assessment"] = remove_premise_targets(payload["evidence_assessment"])
            instructions += (" 待核查前提不是事实；P不能作为外部证据。"
                "clarification_answers是用户确认的研究范围/任务说明，应遵守其范围，不能当作E外部证据或已核实事实，也不需要再次要求用户确认。")
        if record.evidence_assessment and record.evidence_assessment.findings_validated:
            if role == "review":
                instructions += (" F编号是经过原文校验的Agent 2结构化发现，可在affected_ids中定位审查对象，"
                                 "但F本身不是外部证据；解释支持关系时仍应回到其底层E引用。")
            elif role == "world":
                instructions += (" assumption.parent_ids可引用经过原文校验的F作为中间溯源节点；"
                                 "外部事实、world.evidence_refs和actor.visible_evidence_ids仍只能引用E。")
            elif role == "forecast":
                instructions += " 最终报告不能引用F，必须回到E/H/M/S。"
        if isinstance(payload.get("evidence_assessment"), dict):
            # Raw model summaries are audit prose, including on legacy-direct runs.
            payload = dict(payload)
            assessment = EvidenceAssessment.model_validate(payload["evidence_assessment"])
            safe_assessment = {key: value for key, value in payload["evidence_assessment"].items()
                               if key not in {"summary_audit", "rejected_findings"}}
            safe_assessment["summary"] = verified_coverage_summary(assessment)
            payload["evidence_assessment"] = safe_assessment
        if role == "forecast" and payload.get("question", {}).get("mode") == "scenario" and "question_framing" in payload:
            payload = deepcopy(payload)
            proposed = payload["question_framing"].get("proposed_spec")
            if isinstance(proposed, dict):
                payload["question_framing"]["proposed_spec"] = scenario_forecast_context({"question": proposed})["question"]
        if role == "forecast" and schema is DefinitionFirstForecast:
            payload = review_first_view(payload)
        result = model.complete(role, payload, schema, instructions)
        check_cancelled()
        return result

    def question_node(state: FlowState):
        check_cancelled()
        question = QuestionSpec.model_validate(state["question"])
        if record.question_framing:
            frame = record.question_framing
            analysis = QuestionAnalysis(normalized_question=question.question,
                search_queries=[t.query for t in frame.retrieval_plan] or [question.question[:400]])
            return {"question_analysis": analysis.model_dump(mode="json"), "question_framing": frame.model_dump(mode="json")}
        if not record.demo and record.evidence_mode in {"import", "reuse"}:
            # Search terms are only consumed by online retrieval; the user has
            # already supplied both the resolved question and the evidence here.
            analysis = QuestionAnalysis(normalized_question=question.question,
                                        search_queries=[question.question[:400]])
            return {"question_analysis": analysis.model_dump(mode="json")}
        analysis = ask("question", {"question": question.model_dump(mode="json")}, QuestionAnalysis,
                       "用户已确认预测目标和结算规则。用一句话规范化问题，给最多 3 个适合寻找原始资料的检索词；不要自行更改日期或结算条件。"
                       "围绕具名主体已采取的行动、实际结果和决定下一步选择的资源或规则找资料；不要检索情景数量、概率分配等报告格式要求。"
                       "每条检索词聚焦不同实际任务或决策变量，并带实验结果、项目记录、技术文档或规则等具体资料类型；不要把所有主体塞进一个泛泛的‘现状和影响’查询。"
                       "最多3条按用户明确列出的任务分配，明确三个任务时各覆盖一项；主体或工具名不替代任务覆盖，避免重复同一环节。"
                       "全球技术生态优先用英文术语寻找一手论文、项目记录、官方文档或机构规则；当地制度问题使用当地机构与语言。"
                       "数学题短正例为‘AI mathematical conjectures research paper’‘Lean theorem prover official documentation’‘journal generative AI peer review policy’；其他题目替换实际任务与术语，不能套用数学分类。"
                       "‘现状及未来发展趋势’不是原始资料类型，不能代替论文、实验记录、官方文档或机构规则。")
        analysis.search_queries = [q[:400] for q in analysis.search_queries[:3]]
        return {"question_analysis": analysis.model_dump(mode="json")}

    def evidence_node(state: FlowState):
        check_cancelled()
        question = QuestionSpec.model_validate(state["question"])
        if record.question_framing or record.retrieval_result is not None or (record.evidence_mode == "online" and not record.demo):
            retrieval = record.retrieval_result
            if retrieval is None and record.evidence_mode == "online":
                if record.question_framing:
                    tasks = record.question_framing.retrieval_plan
                else:
                    queries = QuestionAnalysis.model_validate(state["question_analysis"]).search_queries
                    queries = list(dict.fromkeys(q[:400] for q in queries[:3] if q.strip()))
                    tasks = [RetrievalTask(id=f"R{i+1:03}", query=query, purpose="background")
                             for i, query in enumerate(queries)]
                tasks = tasks or [RetrievalTask(id="R001", query=question.question[:400], purpose="background")]
                if record.retrieval_started:
                    retrieval = RetrievalResult(status="failed", retrieval_log=[RetrievalLog(task_id=t.id, query=t.query,
                        purpose=t.purpose, status="failed", error="上次取证中断；本运行不重复花费检索额度，请创建新运行")
                        for t in tasks])
                else:
                    record.retrieval_started = True
                    if store:
                        store.save(record)
                    retrieval = retrieve_evidence(question, tasks, data_dir)
            retrieval = retrieval or RetrievalResult(evidence=[e.model_copy(deep=True) for e in imported])
            record.retrieval_result = retrieval
            if store:
                store.save(record)
            evidence_model = model
            if record.demo:
                from .agent12_demo import EvidenceFixtureModel
                evidence_model = EvidenceFixtureModel()
            def save_evidence_progress(assessment):
                record.evidence = [e.model_copy(deep=True) for e in retrieval.evidence]
                record.evidence_assessment = assessment
                if store is not None:
                    # Keep the current stage incomplete until the node returns.
                    store.save(record, snapshot=False)
            assessment = assess_evidence(question, record.question_framing, retrieval, evidence_model, data_dir,
                                         on_progress=save_evidence_progress)
            record.evidence, record.evidence_assessment = retrieval.evidence, assessment
            return {"evidence": [e.model_dump(mode="json") for e in retrieval.evidence],
                    "evidence_assessment": assessment.model_dump(mode="json")}
        if record.evidence_mode == "online":
            queries = QuestionAnalysis.model_validate(state["question_analysis"]).search_queries
            items = online_search(question, data_dir, queries)
        else:
            items = imported
        assessment = ask("evidence", {"question": state["question"], "evidence": evidence_for_model(items)}, EvidenceAssessment,
                         "归纳资料冲突和缺口，只引用实际存在的证据编号。搜索摘要不是全文证据；不可编造新来源。"
                         "缺口只列信息截至时间当时可能取得却未提供的资料；未来结算结果尚未发生是预测对象，不是证据缺口。"
                         "概括已有资料中主体已采取的行动、结果与约束；缺口具体到影响选择的记录或指标，不用‘没有完整原文’替代对可读内容的分析。")
        # Never trust a model-supplied validation flag on the legacy path.
        assessment.findings_validated = False
        check_ids(assessment.evidence_ids, {e.id for e in items}, "证据评估")
        assessment.gaps = [gap for gap in assessment.gaps
                           if not mistakes_future_outcome_for_missing_evidence(gap, question, assume_missing=True)]
        if mistakes_future_outcome_for_missing_evidence(assessment.summary, question):
            assessment.summary = (f"已整理 {len(items)} 条截至信息日的资料；预测期结果尚未发生，"
                                  "应通过有保留的概率表达不确定性。")
        return {"evidence": [e.model_dump(mode="json") for e in items], "evidence_assessment": assessment.model_dump(mode="json")}

    def world_node(state: FlowState):
        check_cancelled()
        question = QuestionSpec.model_validate(state["question"])
        evidence = [Evidence.model_validate(x) for x in state["evidence"]]
        evidence_ids = {e.id for e in evidence}
        assessment = EvidenceAssessment.model_validate(state["evidence_assessment"])
        validated_findings = validated_finding_ids(assessment, evidence_ids)
        parent_ids_allowed = evidence_ids | validated_findings
        world_payload = {
            "question": question.model_dump(mode="json"),
            "evidence": evidence_for_model(evidence, 1600),
            "evidence_assessment": state["evidence_assessment"],
            "valid_evidence_ids": sorted(evidence_ids),
            "valid_finding_ids": sorted(validated_findings),
        }
        world_instructions = (
            "构建条件性的世界状态，不要把预测当作已发生事实。E 才是外部证据；"
            "经过原文校验的 F 可以作为假设的中间溯源节点，但不能假装它是独立来源。"
            "assumption.parent_ids 仅允许 E、经过校验的 F 或本次定义的 H 编号；"
            "world.evidence_refs 和 actor.visible_evidence_ids 必须引用 E。"
            "若无战略主体，可留空 actors 并说明原因。通常选择3–4个有实际决策作用且互不重复的主体，最多4个；根据问题需要可更少，不能为凑数虚构主体。"
            "主体优先覆盖用户关心的互补决策权限，而不是挑证据里出现最多的机构；同类厂商可选一个代表，不得挤掉关键使用者或规则制定者。"
            "例如数学科研题可选科研团队、一个最相关的工具提供者或形式化维护者、期刊编辑、资助方；期刊接收论文与资助拨款是不同权限，不能捏成同一主体。其他题目按各自决策角色选择，不套用数学名单。"
            "缺乏具体机构依据时用明确标注的‘未具名模拟角色’，其立场、资源和约束放在H假设下，不假装已有具体机构政策。"
            "信息截至日之后尚未发生的事件不能陈述为既成事实。"
            "这是Round 0当前局势，state_version=0：summary用3–5句把关键历史事实、已知影响与当前选择空间连起来，每句尽量写清主体、行为或状态、作用机制。"
            "按本题选择3–5个能被行动改变的固定variables，键名简短稳定；值是截至日的具体起点，不重复列每个主体的泛泛活动。"
            "数学题例如猜想产物核验状态、证明检验覆盖范围、期刊采用状态、资助决策状态；其他题目自行选择相应决策变量。"
            "缺证时写‘未知’，不能写‘尚未采用/不存在’；证明工具或竞赛成绩不能证明期刊已有规则，融资新闻不能证明资助机构政策，禁止跨任务归因。"
            "actors用具名机构或可识别的主体群体；代表性构造角色须明说是模型构造，不伪装为现实机构。"
            "goal写主体想争取的利益和要保住什么；resources写实际能力与可控制资源；constraints写战略底线、权限限制和受制于谁。"
            "relations写谁依赖谁的哪项资源、规则或行动，以及利益如何一致或冲突，不只列‘合作/竞争’。"
            "决策倾向与尚未证实的机制放入assumptions，rationale解释依据、适用条件和反证；不要把模型推断冒充主体已公开的立场。"
            "资料缺口只短写到相应变量或假设，不用重复问题或‘没有完整原文’替代当前局势分析。"
        )
        for attempt in range(2):
            world = ask("world", world_payload, WorldState, world_instructions)
            try:
                model_h_ids = {a.id for a in world.assumptions}
                check_ids(world.evidence_refs, evidence_ids | validated_findings, "世界状态")
                for actor in world.actors:
                    check_ids(actor.visible_evidence_ids, evidence_ids | validated_findings, "主体画像")
                for assumption in world.assumptions:
                    check_ids(assumption.parent_ids, parent_ids_allowed | model_h_ids, "假设")
                break
            except ValueError as exc:
                if attempt:
                    raise
                world_payload["validation_feedback"] = str(exc)
                world_payload["invalid_output"] = world.model_dump(mode="json")
        world.actors = world.actors[:4]
        canonicalize_world_ids(world)
        allowed_conditions = set(question.user_assumptions)
        if record.question_framing:
            allowed_conditions |= {p.content for p in record.question_framing.premises
                if p.user_review == "retained" and p.treatment == "scenario_condition"}
            for a in world.assumptions:
                if a.created_by == "user" and a.content not in allowed_conditions:
                    a.created_by = "model"
                    a.rationale = "模型提出，未经用户指定为情景条件；" + a.rationale
        mapping = {}
        conditions = [(None, text) for text in question.user_assumptions]
        if record.question_framing:
            conditions += [(p.id, p.content) for p in record.question_framing.premises
                           if p.user_review == "retained" and p.treatment == "scenario_condition"]
        for premise_id, content in conditions:
            assumption = next((a for a in world.assumptions if a.content == content), None)
            if assumption is None:
                existing = {a.id for a in world.assumptions}
                number = 1
                while f"H{number:03}" in existing:
                    number += 1
                assumption = Assumption(id=f"H{number:03}", created_by="user", content=content,
                    rationale="用户明确指定的情景条件，不是已证实事实")
                world.assumptions.append(assumption)
            assumption.created_by = "user"
            if premise_id:
                mapping[premise_id] = assumption.id
        record.premise_assumption_map = mapping
        evidence_ids = {e.id for e in evidence}
        assessment = EvidenceAssessment.model_validate(state["evidence_assessment"])
        finding_ids = validated_finding_ids(assessment, evidence_ids)
        finding_aliases = finding_evidence_map(state.get("evidence_assessment"))
        world.evidence_refs, dropped_refs = resolve_refs(world.evidence_refs, evidence_ids, expansions=finding_aliases)
        for actor in world.actors:
            actor.visible_evidence_ids, dropped = resolve_refs(
                actor.visible_evidence_ids, evidence_ids, expansions=finding_aliases
            )
            dropped_refs += dropped
        # Unlike raw source references, assumption parent_ids intentionally retain
        # validated F IDs as intermediate provenance nodes.
        valid_parents = evidence_ids | finding_ids | {a.id for a in world.assumptions}
        for assumption in world.assumptions:
            assumption.parent_ids, dropped = resolve_refs(assumption.parent_ids, valid_parents)
            dropped_refs += dropped
        if dropped_refs:
            record.errors.append(
                f"世界状态丢弃无法解析的引用：{', '.join(dict.fromkeys(dropped_refs))}"
            )
        return {"world": world.model_dump(mode="json"), "premise_assumption_map": mapping}

    def simulation_node(state: FlowState):
        check_cancelled()
        question = QuestionSpec.model_validate(state["question"])
        world = WorldState.model_validate(state["world"])
        evidence = [Evidence.model_validate(x) for x in state["evidence"]]
        if not world.actors:
            return {"actions": [], "simulation": []}
        actions, steps = [], []
        current = world.model_dump(mode="json")
        for round_number in (1, 2):
            check_cancelled()
            parent = round_number - 1
            def actor_call(actor):
                visible = evidence_for_model([e for e in evidence if e.id in actor.visible_evidence_ids], 1000)
                actor_evidence_ids = {e["id"] for e in visible}
                actor_h_ids = {a.id for a in world.assumptions}
                actor_payload = {
                    "question": state["question"], "actor": actor.model_dump(),
                    "state": current, "visible_evidence": visible, "round": round_number,
                    # Only completed prior-round decisions are visible. Peers in
                    # this round still decide independently and concurrently.
                    "previous_round_actions": [
                        a.model_dump(mode="json", include={"id", "actor_id", "action", "conditions", "rationale_summary"})
                        for a in actions if a.round == round_number - 1
                    ],
                    "previous_round_unresolved": list(steps[-1].unresolved) if steps else [],
                    "valid_evidence_ids": sorted(actor_evidence_ids),
                    "valid_assumption_ids": sorted(actor_h_ids),
                }
                actor_instructions = (
                    "你只扮演actor所指定的现实主体，根据其目标、资源、权限和约束选择一项可执行的现实行动，说明行动对象与实施方式。"
                    "你不是替用户撰写研究报告的助手：问题里的情景数量、概率分配和报告格式是本研究的交付要求，"
                    "不得把‘提出本研究的情景推演’‘给出概率’或‘完成本研究报告’当作主体行动。"
                    "主体自身开展实验、部署工具、调整资助或评审流程等现实工作可以作为行动。"
                    "仅做一次条件性行动；evidence_ids 只允许 valid_evidence_ids 中的 E，"
                    "assumption_ids 只允许 valid_assumption_ids 中的 H。H 是假设而不是证据。"
                    "不得声称预测期计划或假设的行动已经发生。"
                    "action写明该主体对谁或什么实施哪项具体行动、动用什么能力或资源，行动规模不得超出其权限；不要只写‘加强合作/推动创新’。"
                    "rationale_summary用2–3句结合当前state的具体变量解释利益取舍、受哪些证据和约束影响、为何此时采取该行动。"
                    "expected_impact是主体预期，不是本轮已实现的结果；写行动希望如何改变已有状态变量、依赖谁的响应、谁可能受益或受损，以及何种条件下失效；未知幅度用定性表达，不编造数值。"
                    "conditions写行动成立的具体前提。第二轮依据上一轮state的新变化调整选择；previous_round_actions和previous_round_unresolved仅为已完成的上一轮，不能预知本轮同伴行动。"
                    "第二轮rationale_summary先点明上一状态或卡点，再说明本轮如何回应；已经完成的动作不重复宣布，不能机械复制第一轮。"
                    "可以等待或维持已有安排，但要指明在等谁的哪项产物或哪项条件；若继续同一行动，说明为何仍有效及实施上有何推进。"
                )
                for attempt in range(2):
                    action = ask("actor", actor_payload, ActorAction, actor_instructions,
                                 actor_id=actor.id, round_number=round_number)
                    try:
                        check_ids(action.evidence_ids, actor_evidence_ids, "主体行动")
                        check_ids(action.assumption_ids, actor_h_ids, "主体行动假设")
                        break
                    except ValueError as exc:
                        if attempt:
                            raise
                        actor_payload["validation_feedback"] = str(exc)
                        actor_payload["invalid_output"] = action.model_dump(mode="json")
                action.id = f"M{round_number}-{actor.id}"
                action.created_by = actor.id
                action.actor_id = actor.id
                action.round = round_number
                action.parent_state = parent
                action.parent_ids = [f"S{parent}"]
                action.kind = "simulation"
                action.evidence_ids, _ = resolve_refs(action.evidence_ids, {e["id"] for e in visible})
                action.assumption_ids, _ = resolve_refs(action.assumption_ids, {a.id for a in world.assumptions})
                return action
            with ThreadPoolExecutor(max_workers=min(4, len(world.actors))) as pool:
                round_actions = list(pool.map(actor_call, world.actors))
            actions.extend(round_actions)
            allowed_variables = set(current.get("variables", {}))
            step_payload = {"question": state["question"], "state": current, "actions": [a.model_dump(mode="json") for a in round_actions],
                            "round": round_number, "allowed_variable_keys": sorted(allowed_variables)}
            step = ask("environment", step_payload, SimulationStep,
                       f"联合处理全部行动；保留冲突与条件。当前信息截至时间为 {question.as_of.isoformat()}。"
                       "你只推演这些现实主体行动对世界状态的影响，不替用户编写情景报告；情景数量、概率和输出格式不是世界事件或状态变量。"
                       "若某项行动只是描述本研究该怎样输出报告，不能据此制造现实变化，应在unresolved说明其缺少可执行行为。"
                       "此后状态只能用‘若...则...’的条件式描述，绝不能把计划或模拟结果写成已发生的历史事实。"
                       "state_changes 的键只能从 allowed_variable_keys 中选择，不得新增变量名；"
                       "如需提出新维度，请写在 summary 或 unresolved 中。"
                       "summary明确写‘本轮模拟’，用3–5句写本轮局势如何不同于输入state：点名至少两个相关行动怎样互相促进或抵消、谁受益或受损、哪个约束随之改变；不足两个行动时按实际行动说明。"
                       "state_changes的值写清‘原状态→条件成立后的新状态；由谁的什么行动通过何种机制造成’，前值承接输入state的现值（可忠实简述），不能每轮都退回Round 0起点。"
                       "区分计划、同意、执行、观测效果；没有新执行或新结果时不重复计算上一轮增量，不只复述动作或笼统写‘改善/恶化’，没有变化的变量无需填入。"
                       "expected_impact只是主体预期，不能直接抄为state_changes；每项变化说明本轮哪个action触发，必要条件由输入state、对方action或明确H中的哪项支持。"
                       "条件既未满足也未明确假设时，保留原变量状态，在unresolved写具体卡点；允许state_changes为空，不为凑变化虚构进展。"
                       "涉及其他主体采用、认可或配合，须有对方action或明确H支持；H不能覆盖对方明确拒绝或已有约束，矛盾写入conflicts。"
                       "对方同意试点只代表同意，未执行、未测量时不能写成已提高效率、准确率或采用效果；即使模拟假设了改善，也须保留假设条件，不能称测得改善。"
                       "conflicts写具体主体之间的利益、资源或规则冲突；unresolved只列决定下一步走向的未决条件。"
                       "可以推演定性变化，不能凭空生成效果百分比、发布日期或已实现成果；第二轮必须接续上一轮变量状态，而不是重新概述原问题。", round_number=round_number)
            unknown_variables = set(step.state_changes) - allowed_variables
            if unknown_variables:
                step.state_changes = {key: value for key, value in step.state_changes.items() if key in allowed_variables}
                step.unresolved.append(f"模型提出未定义变量 {', '.join(sorted(unknown_variables))}；未写入状态。")
            step.id = f"S{round_number}"
            step.parent_ids = [a.id for a in round_actions]
            step.round = round_number
            step.parent_state = parent
            step.next_state = round_number
            step.kind = "simulation"
            step.evidence_ids, _ = resolve_refs(
                step.evidence_ids, {e.id for e in evidence},
                expansions=finding_evidence_map(state.get("evidence_assessment")),
            )
            step.assumption_ids, _ = resolve_refs(step.assumption_ids, {a.id for a in world.assumptions})
            steps.append(step)
            current = {**current, "state_version": round_number, "variables": {**current.get("variables", {}), **step.state_changes}, "simulation_summary": step.summary}
        return {"actions": [a.model_dump(mode="json") for a in actions], "simulation": [s.model_dump(mode="json") for s in steps]}

    def review_node(state: FlowState):
        check_cancelled()
        question = QuestionSpec.model_validate(state["question"])
        evidence = [Evidence.model_validate(x) for x in state["evidence"]]
        world = WorldState.model_validate(state["world"])
        evidence_ids_for_review = {e.id for e in evidence}
        assessment_for_review = EvidenceAssessment.model_validate(state["evidence_assessment"])
        trusted_findings = validated_finding_ids(assessment_for_review, evidence_ids_for_review)
        valid_review_ids = (
            evidence_ids_for_review | trusted_findings |
            {a.id for a in world.assumptions} | {a.id for a in world.actors} |
            {a["id"] for a in state["actions"]} | {item["id"] for item in state["simulation"]}
        )
        review_payload = {
            "question": state["question"], "evidence": evidence_for_model(evidence, 1200),
            "evidence_assessment": state["evidence_assessment"], "world": state["world"],
            **trace_for_model(state), "valid_affected_ids": sorted(valid_review_ids),
        }
        review_instructions = (
            "检查证据是否支持关键判断、遗漏反证和模拟跳步。最多五个重点问题。"
            "只核对上下文给出的已选引句和经过校验的F，不声称重新阅读了完整网页；选文未覆盖的内容保持未知。"
            "每个问题的explanation用2–3句定位到具体主体行动、状态变量或因果连接，交代原始依据、推断缺口、会使哪条条件路径更强或更弱，并给出可补充的具体资料。"
            "优先审查真正会改变决策或路径排序的缺口；不要重复问题、罗列抽象风险，或用‘没有完整原文’替代对已有来源和两轮变化的分析。"
            "审查时关注 evidence_assessment.quality_profile 的来源覆盖和限制；"
            "来源组不等于独立认证，来源数量不能自动修改概率或成为 blocked 判据。"
            "不要求预测期尚未发生的结果作为证据。affected_ids 只能使用 valid_affected_ids；"
            "F 仅用于定位已验证 Finding，不等于新外部证据；禁止 R 检索任务编号。"
        )
        for attempt in range(2):
            review = ask("review", review_payload, Review, review_instructions)
            invalid = sorted({
                rid for issue in review.issues
                if not mistakes_future_outcome_for_missing_evidence(
                    f"{issue.claim} {issue.explanation}", question
                )
                for rid in canonical_ids(issue.affected_ids, valid_review_ids)
                if rid not in valid_review_ids
            })
            if not invalid:
                break
            if attempt:
                raise ValueError("审查意见引用了不存在的节点：" + ", ".join(invalid))
            review_payload["validation_feedback"] = {
                "invalid_affected_ids": invalid,
                "allowed_ids": sorted(valid_review_ids),
                "instruction": "删除无效引用，或改用 allowed_ids 中的真实编号。",
            }
        future_gap_found = any(mistakes_future_outcome_for_missing_evidence(
            f"{issue.claim} {issue.explanation}", question) for issue in review.issues)
        future_gap_found |= any(mistakes_future_outcome_for_missing_evidence(x, question, assume_missing=True)
                                for x in review.missing_evidence)
        future_gap_found |= any(mistakes_future_outcome_for_missing_evidence(x, question)
                                for x in review.unsupported_claims)
        future_gap_found |= mistakes_future_outcome_for_missing_evidence(world.summary, question)
        review.issues = [issue for issue in review.issues if not mistakes_future_outcome_for_missing_evidence(
            f"{issue.claim} {issue.explanation}", question)]
        review.missing_evidence = [x for x in review.missing_evidence
                                   if not mistakes_future_outcome_for_missing_evidence(x, question, assume_missing=True)]
        review.unsupported_claims = [x for x in review.unsupported_claims
                                     if not mistakes_future_outcome_for_missing_evidence(x, question)]
        if future_gap_found:
            review.issues.append(ReviewIssue(
                severity="medium", claim="预测期结果未知应由概率表达",
                explanation="审查排除了要求未来行情的判断；概率仅使用截至日证据。"))
        if not evidence:
            review.status = "blocked"
            review.issues.append(ReviewIssue(severity="high", claim="证据包为空", explanation="没有可核查的外部证据，不能给概率。"))
        if all(e.source_type == "snippet_only" for e in evidence) and evidence:
            review.issues.append(ReviewIssue(severity="medium", claim="来源仅有搜索片段", explanation="未取得原文，结论需保留限制。"))
        if any(issue.severity == "high" for issue in review.issues) or review.unsupported_claims:
            review.status = "blocked"
        elif future_gap_found and review.status == "blocked":
            review.status = "qualified"
        evidence_ids = {e.id for e in evidence}
        assessment = EvidenceAssessment.model_validate(state["evidence_assessment"])
        finding_ids = validated_finding_ids(assessment, evidence_ids)
        valid = (evidence_ids | finding_ids | {a.id for a in world.assumptions} | {a.id for a in world.actors}
                 | {a["id"] for a in state["actions"]} | {s["id"] for s in state["simulation"]})
        for issue in review.issues:
            sanitize_review_issue_ids(issue, valid)
        review.probability_basis = "full" if review.status != "blocked" else "none"
        if (review.status == "blocked" or future_gap_found) and question.mode == "binary" and evidence:
            audit = ask("evidence_audit", {"question": state["question"], "evidence": evidence_for_model(evidence, 1200),
                                           "evidence_assessment": state["evidence_assessment"]}, EvidenceOnlyAudit,
                        "仅审查信息截至日已有的外部证据，不使用世界状态、假设、主体行动或模拟结果。"
                        "source_type=exercise 表示用户事后整理的历史练习资料，不代表内容虚构；须有截至日前的发布日期，"
                        "但不是当时冻结的盲回测，应提示回看偏差。"
                        "判断是否足以给一个有保留、未经校准的主观概率。未来结果尚未发生、资料仅有一两个来源或存在延期风险，"
                        "都不是自动阻断理由，应通过不确定的概率表达；若证据本身为空、晚于截至日、无法核查或不支持问题，才设 can_estimate=false。")
            effective_can_estimate, effective_reasons, discarded_reasons = sanitize_evidence_audit(
                audit, question, evidence)
            review.evidence_audit_model_can_estimate = audit.can_estimate
            review.evidence_audit_can_estimate = effective_can_estimate
            review.evidence_audit_blocking_reasons = effective_reasons
            review.evidence_audit_discarded_reasons = discarded_reasons
            if effective_can_estimate and all(available_at_cutoff(e, question.as_of) for e in evidence):
                review.probability_basis = "evidence_only"
            elif future_gap_found:
                review.status = "blocked"
                review.probability_basis = "none"
        return {"review": review.model_dump(mode="json")}

    def forecast_node(state: FlowState):
        check_cancelled()
        question = QuestionSpec.model_validate(state["question"])
        evidence = [Evidence.model_validate(x) for x in state["evidence"]]
        world = WorldState.model_validate(state["world"])
        review = Review.model_validate(state["review"])
        evidence_only = question.mode == "binary" and review.probability_basis == "evidence_only"
        price_context = market_price_context(question, evidence)
        if evidence_only and config.SHADOW_FULL:
            full_world = world
            full_simulation = [SimulationStep.model_validate(x) for x in state["simulation"]]
            full_payload = {
                "question": state["question"],
                "evidence": evidence_for_model(evidence, 900),
                "evidence_assessment": state["evidence_assessment"],
                "world": state["world"],
                **trace_for_model(state),
                "review": state["review"],
                "valid_evidence_ids": [e.id for e in evidence],
                "valid_assumption_ids": [a.id for a in full_world.assumptions],
                "valid_simulation_ids": [step.id for step in full_simulation],
            }
            full_instructions = (
                "只用已给资料与审查过的判断。支持/反对的每条主张必须至少引用"
                "一个有效证据、假设或模拟编号；无法引用的主张请删除。"
            )
            full_probability_instructions = (
                f"概率键必须严格为 {question.outcomes}，数值在 0 到 1 且合计为 1；"
                "概率是未经校准的主观判断；证据不足或开放问题必须用 null。"
            )
        if evidence_only:
            world = WorldState(summary="仅使用事前外部证据")
            simulation = []
            forecast_payload = {"question": state["question"], "evidence": evidence_for_model(evidence, 900),
                                "valid_evidence_ids": [e.id for e in evidence],
                                "valid_assumption_ids": [], "valid_simulation_ids": []}
            instructions = ("这是二元事件的事前预测，必须给出非 null 的是/否主观概率。"
                            "仅使用给定的截至日外部证据，完全忽略世界建模、审查推断、模拟和未经核实的假设；"
                            "支持与反对的每条主张只能引用有效的 E 编号。"
                            "预测期结果尚未发生是预测目标，不能要求它作为事前证据。"
                            "证据有限时把不确定性体现在接近中性的概率中，并说明来源和回看偏差；不得假装概率已校准。")
            probability_instructions = (f"概率键必须严格为 {question.outcomes}，数值在 0 到 1 且合计为 1，"
                                        "probabilities 不能为 null。")
            forecast_schema = EvidenceOnlyForecast
        else:
            simulation = [SimulationStep.model_validate(x) for x in state["simulation"]]
            forecast_payload = {"question": state["question"], "evidence": evidence_for_model(evidence, 900),
                                "evidence_assessment": state["evidence_assessment"], "world": state["world"],
                                **trace_for_model(state), "review": state["review"],
                                "valid_evidence_ids": [e.id for e in evidence],
                                "valid_assumption_ids": [a.id for a in world.assumptions],
                                "valid_simulation_ids": [s["id"] for s in state["simulation"]]}
            instructions = "只用已给资料与审查过的判断。支持/反对的每条主张必须至少引用一个有效证据、假设或模拟编号；无法引用的主张请删除。"
            probability_instructions = (f"概率键必须严格为 {question.outcomes}，数值在 0 到 1 且合计为 1；"
                                        "概率是未经校准的主观判断；证据不足或开放问题必须用 null。")
            forecast_schema = Forecast
        instructions += (" 区分来源事实、假设与模拟；对未核实关系和数值说明限制，不把模型推演称为实测。"
                         "只核对上下文已选引句和经过校验的F，不声称重新阅读完整网页；报告仍按有效E/H/S归属依据。")
        if question.mode == "scenario":
            forecast_payload = scenario_forecast_context(forecast_payload)
            record.forecast_policy = {**record.forecast_policy, **deepcopy(forecast_payload.get("forecast_policy", {}))}
            forecast_schema = ScenarioForecast if evidence else Forecast
            probability_instructions = (
                "这是开放场景研究：已有证据时必须给出非null的probabilities，数值在0到1之间、合计为1。"
                "先固定同一目标日期、研究范围和单一判定轴，再划分至少两个具名、互斥且覆盖主要可能性的终局；"
                "按问题需要决定情景数量，用户明确要求三个时给三个。判定轴可用采用程度、完成状态等实际结果，不按时间轮次分组。"
                "S1/S2等是同一条模拟轨迹的先后状态，可以同时发生，不能作为概率键、情景名或直接复制摘要充当终局定义。"
                "scenario_details与概率键逐项同名：definition说明目标时点主体做法与关键变量状态，按统一判定轴区分边界；"
                "conditions列2–4项驱动条件，说明重叠时的终局判定顺序。这些是驱动因素，不是彼此不同前提下的条件成功率。"
                "probabilities是当前共同信息条件下各互斥终局的未经校准主观权重，不能将各自条件成功率归一化。"
                "rationale用2–4句连接Round 0、已模拟行动交互和该终局为何更可能或更不可能；区分来源事实、模拟路径及剩余不确定性。"
                "同一S可以支持多个终局，只在simulation_ids登记该情景文字实际用到的S；evidence_ids/assumption_ids同样对应实际依据。"
                "若已有outcomes满足上述定义可沿用。概率不是来源实测频率；证据有限或review=blocked写入limitations，不因此拒绝概率。")
            if evidence:
                # Private output definitions precede weights in the same call.
                # Public API records continue to contain ordinary Forecasts.
                forecast_schema = DefinitionFirstForecast
                forecast_payload["terminal_task"] = definition_first_task(forecast_payload["question"])
                probability_instructions = WIRE_INSTRUCTIONS
                manual_feedback = record.report_repair_audit.get("validation_feedback")
                if manual_feedback:
                    forecast_payload["validation_feedback"] = manual_feedback
                record.forecast_policy["wire_format"] = WIRE_FORMAT
            else:
                probability_instructions = "尚未取得来源，probabilities必须为null；说明需要补充什么资料，不编造概率。"
            instructions += (
                "优先完成一份可读报告：conclusion直接回答哪些现实做法可能改变、由谁推动、为何形成这些路径，不复述用户要求。"
                "scenarios简述各条路径的主体行动与局势变化，并与终态定义和权重保持一致。"
                "supporting/opposing分别解释具体事实或条件如何推动、阻碍某条路径；有H/S依据的机制明说是条件推演。"
                "终态rationale使用S结果时明确‘在该假设/模拟路径下’；E只支持其实际记载的前提，不能将同段的S结果改写为已发生历史事实。"
                "new_information列值得跟踪的具体行动、指标或规则变化，以及出现后会提高或降低哪条路径的相对概率。"
                "limitations只简明说明会影响上述判断的实际缺口，不以笼统不确定性或‘没有完整原文’充当报告正文。"
                "模型假设、模拟结果及被遮蔽的比例不是新增观测，不要声称其经过实测。"
                "原文来源、发布日期未知及历史回看限制必须保留；审查意见作为限制披露。")
        if price_context:
            forecast_payload["market_price_context"] = price_context
            price_note = ("对于市场价格问题，先比较预测期限与历史价格覆盖：一两日涨势不能直接外推到月末，"
                          "高振幅也可能意味着回撤；宏观指标与指数涨跌之间不能直接画等号。"
                          "没有查到利空消息不是上涨证据。若缺少同期限历史基准率、波动和估值资料，"
                          "不要把微弱证据表达成明显的方向优势；在局限中指出缺少哪些事前资料。"
                          "历史练习绝不可使用信息截至日之后的实际结果。")
            instructions += price_note
            if evidence_only and config.SHADOW_FULL:
                full_payload["market_price_context"] = price_context
                full_instructions += price_note
        usable_candidates = []
        last_failure = "模型未返回可用报告"
        for attempt in range(2):
            wire_audit = None
            try:
                response = ask("forecast", forecast_payload, forecast_schema,
                               instructions + "解释应充分且具体：结论2–4句，支持与反对主张说明来源联系和限制，每个情景应能独立读懂；避免只写标题或重复套话。" + probability_instructions)
                if isinstance(response, DefinitionFirstForecast):
                    wire_audit = {"wire_format": WIRE_FORMAT, "candidate": response.model_dump(mode="json"), "validation_errors": []}
                    record.forecast_policy.setdefault("wire_attempts", []).append(wire_audit)
                    # Check raw labels/definitions before partition text is added.
                    validate_scenario_end_states(response, simulation)
                forecast = to_public_forecast(response)
            except ModelCancelled:
                raise
            except (ValueError, RuntimeError, BudgetExceeded) as exc:
                if question.mode != "scenario":
                    raise
                last_failure = f"{type(exc).__name__}: {exc}"
                if wire_audit is not None:
                    wire_audit["validation_errors"].append(last_failure)
                record.errors.append(f"报告生成尝试{attempt + 1}失败：{last_failure}")
                forecast_payload["validation_feedback"] = f"请重新输出符合JSON结构的完整报告：{last_failure}"
                if store is not None:
                    store.save(record, snapshot=False)
                if isinstance(exc, BudgetExceeded):
                    break
                continue
            candidate = forecast.model_copy(deep=True)
            forecast.probability_basis = "evidence_only" if evidence_only else "full"
            canonicalize_forecast_ids(forecast, evidence, world, simulation,
                                      state.get("evidence_assessment"))
            if wire_audit is not None:
                qualify_model_claims(forecast)
            if evidence_only:
                # The evidence-only report cannot acquire new model assumptions.
                # Keep evidence-backed prose, but remove references to the excluded branch.
                forecast.key_assumptions = []
                for claim in forecast.supporting + forecast.opposing:
                    claim.assumption_ids = []
                    claim.simulation_ids = []
                forecast.supporting = [claim for claim in forecast.supporting if claim.evidence_ids]
                forecast.opposing = [claim for claim in forecast.opposing if claim.evidence_ids]
                forecast.limitations = [item for item in forecast.limitations
                                        if not mistakes_future_outcome_for_missing_evidence(item, question)]
            try:
                validate_forecast(forecast, question, evidence, world, simulation, review,
                                  require_probability=evidence_only or (question.mode == "scenario" and bool(evidence)))
                record.forecast_attempts.append(ForecastAttempt(candidate=candidate))
                if evidence_only:
                    forecast.limitations.append("概率仅依据截至日已有证据，未采用模拟行动或建模假设。")
                if evidence_only and config.SHADOW_FULL:
                    # Disabled in the product by default. Shadow failures cannot
                    # invalidate or silently replace the already validated main forecast.
                    shadow = None
                    shadow_review = review.model_copy(update={"status": "passed", "probability_basis": "full"})
                    try:
                        shadow = ask(
                            "forecast", full_payload, Forecast,
                            full_instructions + "语言简洁。" + full_probability_instructions,
                        )
                        shadow.probability_basis = "full"
                        canonicalize_forecast_ids(shadow, evidence, full_world, full_simulation,
                                                  state.get("evidence_assessment"))
                        ev_ids = {e.id for e in evidence}
                        as_ids = {a.id for a in full_world.assumptions}
                        sim_ids = {step.id for step in full_simulation}
                        shadow.key_assumptions = [sid for sid in shadow.key_assumptions if sid in as_ids]
                        for claim in shadow.supporting + shadow.opposing:
                            claim.evidence_ids = [eid for eid in claim.evidence_ids if eid in ev_ids]
                            claim.assumption_ids = [aid for aid in claim.assumption_ids if aid in as_ids]
                            claim.simulation_ids = [sid for sid in claim.simulation_ids if sid in sim_ids]
                        shadow.supporting = [claim for claim in shadow.supporting
                                             if claim.evidence_ids or claim.assumption_ids or claim.simulation_ids]
                        shadow.opposing = [claim for claim in shadow.opposing
                                           if claim.evidence_ids or claim.assumption_ids or claim.simulation_ids]
                        validate_forecast(shadow, question, evidence, full_world, full_simulation, shadow_review)
                        record.shadow_forecast = shadow
                    except ModelCancelled:
                        raise
                    except (ValueError, RuntimeError, BudgetExceeded) as exc:
                        if shadow is not None:
                            try:
                                record.shadow_forecast = repair_forecast(
                                    shadow, question, evidence, full_world, full_simulation,
                                    shadow_review, str(exc)
                                )
                                record.errors.append(f"影子 full 预测经兜底修复：{type(exc).__name__}")
                            except (ValueError, RuntimeError, BudgetExceeded) as inner:
                                record.errors.append(f"影子 full 预测失败：{type(inner).__name__}")
                        else:
                            record.errors.append(f"影子 full 预测失败：{type(exc).__name__}")
                if (price_context and price_context["forecast_horizon_days"] >= 14
                        and price_context["dated_price_observation_days"] <= 3):
                    forecast.limitations.append(
                        f"证据包中明确标注事件日的价格资料只有 {price_context['dated_price_observation_days']} 个交易日，"
                        f"预测跨度约 {price_context['forecast_horizon_days']} 天；未据此验证同期限历史基准率或波动分布。")
                if any(e.source_type == "exercise" and e.retrieved_at > question.as_of for e in evidence):
                    forecast.limitations.append("历史演练资料是事后按发布日期整理，并非截至日冻结快照；可能存在回看偏差。")
                return {"forecast": forecast.model_dump(mode="json")}
            except ValueError as exc:
                last_failure = str(exc)
                if wire_audit is not None:
                    wire_audit["validation_errors"].append(last_failure)
                usable_candidates.append(forecast.model_copy(deep=True))
                record.forecast_attempts.append(ForecastAttempt(candidate=candidate, validation_errors=[str(exc)]))
                # Persist the rejected public output before another request can fail.
                if store is not None:
                    store.save(record, snapshot=False)
                forecast_payload["validation_feedback"] = f"上次报告未通过校验：{exc}。请修正后重新输出完整 JSON。"
        if question.mode != "scenario":
            raise ValueError("报告内容或引用校验未通过：" + last_failure)
        # Prefer a structurally valid candidate with real references, even when the
        # model omitted probabilities. Never synthesize or normalize model numbers.
        for fallback in reversed(usable_candidates):
            try:
                validate_forecast(fallback, question, evidence, world, simulation, review)
            except ValueError:
                continue
            fallback.status = "partial" if fallback.probabilities is None else "completed"
            fallback.limitations.append("自动报告未完整满足输出要求，保留了已通过结构和引用校验的候选；未补造概率。" + last_failure)
            return {"forecast": fallback.model_dump(mode="json")}
        fallback = Forecast(status="partial", conclusion=("已保留可追溯的来源；自动报告未完整生成，未编造概率。" if evidence
                                                          else "未取得可用来源；自动报告未完整生成，未编造概率。"),
            supporting=[{"text": f"已取得来源：{item.title}", "evidence_ids": [item.id]} for item in evidence],
            limitations=["自动报告两次纠正后仍未通过基础结构、引用或概率校验：" + last_failure])
        validate_forecast(fallback, question, evidence, world, simulation, review)
        return {"forecast": fallback.model_dump(mode="json")}

    graph = StateGraph(FlowState)
    for name, fn in (("define_question", question_node), ("retrieve", evidence_node), ("model_world", world_node), ("simulate", simulation_node), ("audit", review_node), ("synthesize", forecast_node)):
        graph.add_node(name, fn)
    graph.add_edge(START, start_at)
    graph.add_edge("define_question", "retrieve")
    graph.add_edge("retrieve", "model_world")
    graph.add_edge("model_world", "simulate")
    graph.add_edge("simulate", "audit")
    graph.add_edge("audit", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


def execute(record: RunRecord, imported: list[Evidence], store, *, resume: bool = False, cancel_event=None):
    model = None
    stage_names = [stage for stage, _ in STAGE_NODES]
    current_stage = stage_names[0]
    stage_started = time.monotonic()
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("运行已取消")
        if resume:
            current_stage = "evidence"
            restore_legacy_evidence_stage(record, store.directory)
            if not record.demo:
                verify_saved_evidence_stage(record, store.directory)
            # Resume can skip world/review entirely. Enforce the evidence gate
            # before choosing a later checkpoint or constructing a model client.
            # The persisted stage, not a possibly stale top-level copy, is authoritative.
            saved_stage = record.stage_outputs.get("evidence")
            saved_assessment = saved_stage.get("evidence_assessment") if isinstance(saved_stage, dict) else None
            if (isinstance(saved_assessment, dict) and saved_assessment.get("findings")
                    and saved_assessment.get("findings_validated") is not True):
                raise ValueError("保存的证据发现未经过原文校验，不能继续后续阶段；已保留原记录，请重新导入原文创建运行。")
        if record.demo:
            model = None
        else:
            previous_calls = store.list_calls(record.run_id)
            prior_usage = {"calls": max(len(previous_calls), record.usage["calls"]),
                "prompt_tokens": max(sum(c.prompt_tokens for c in previous_calls), record.usage["prompt_tokens"]),
                "completion_tokens": max(sum(c.completion_tokens for c in previous_calls), record.usage["completion_tokens"])}
            cap = max(0, config.MAX_CALLS - unique_request_count(record.preparation_records))
            if record.report_repair_parent:
                cap = min(cap, 2)
            prep_seconds = sum(c.elapsed_seconds for c in record.preparation_records)
            retrieval_seconds = (max((log.elapsed_seconds for log in record.retrieval_result.retrieval_log), default=0)
                                 if record.retrieval_result and not record.report_repair_parent else 0)
            measured_runtime = request_active_seconds(previous_calls) + retrieval_seconds
            model = ModelClient(initial_usage=prior_usage, cancel_event=cancel_event,
                initial_active_seconds=prep_seconds + max(record.active_seconds, measured_runtime),
                call_limit=cap, on_reserve=lambda h, v: store.reserve_call(record.run_id, "runtime", call_limit=cap,
                    input_hash=h, prompt_version=v), on_finish=store.finish_call)
        state: FlowState = {"question": record.question.model_dump(mode="json")}
        if record.question_framing:
            state["question_framing"] = record.question_framing.model_dump(mode="json")
        start_at = "define_question"
        if resume:
            pending = next(((stage, node) for stage, node in STAGE_NODES if stage not in record.stage_outputs), None)
            if pending is None:
                raise ValueError("所有阶段已有快照，无法继续")
            for stage, _ in STAGE_NODES:
                if stage == pending[0]:
                    break
                state.update(record.stage_outputs[stage])
            start_at = pending[1]
            current_stage = pending[0]
            record.retry_history.extend(record.errors)
            record.errors = []
            record.resume_count += 1
            record.finished_at = None
        graph = build_graph(record, imported, model, store.directory, start_at=start_at, store=store, cancel_event=cancel_event)
        record.status = "running"
        record.stage = current_stage
        record.failed_stage = None
        store.save(record)
        stage_started = time.monotonic()
        for update in graph.stream(state, stream_mode="updates"):
            if cancel_event is not None and cancel_event.is_set():
                raise ModelCancelled("运行已取消")
            node, output = next(iter(update.items()))
            stage = {"define_question": "question", "retrieve": "evidence", "model_world": "world", "simulate": "simulation", "audit": "review", "synthesize": "forecast"}[node]
            record.stage_durations[stage] = round(time.monotonic() - stage_started, 3)
            state.update(output)
            record.stage_outputs[stage] = output
            if "question_analysis" in output:
                record.question_analysis = QuestionAnalysis.model_validate(output["question_analysis"])
            if "evidence" in output:
                record.evidence = [Evidence.model_validate(x) for x in output["evidence"]]
                record.evidence_assessment = EvidenceAssessment.model_validate(output["evidence_assessment"])
            if "world" in output:
                record.world = WorldState.model_validate(output["world"])
            if "actions" in output:
                record.actions = [ActorAction.model_validate(x) for x in output["actions"]]
                record.simulation = [SimulationStep.model_validate(x) for x in output["simulation"]]
            if "review" in output:
                record.review = Review.model_validate(output["review"])
            if "forecast" in output:
                record.forecast = Forecast.model_validate(output["forecast"])
            if model:
                record.usage = model.usage.copy()
                record.model = getattr(model, "actual_model", None) or record.model
                if hasattr(model, "active_seconds"):
                    record.active_seconds = max(record.active_seconds, model.active_seconds-sum(c.elapsed_seconds for c in record.preparation_records))
            next_index = stage_names.index(stage) + 1
            current_stage = stage_names[next_index] if next_index < len(stage_names) else "done"
            record.stage = current_stage
            store.save(record)
            stage_started = time.monotonic()
        record.status = record.forecast.status
        record.stage = "done"
    except ModelCancelled as exc:
        record.status = "cancelled"
        record.stage = "cancelled"
        record.failed_stage = current_stage
        record.errors.append(str(exc))
    except EvidenceStageError as exc:
        record.evidence = exc.result.evidence
        record.retrieval_result = exc.result
        record.evidence_assessment = exc.assessment
        record.status = "failed"
        record.stage = "failed"
        record.failed_stage = current_stage
        record.errors.append(str(exc))
    except BudgetExceeded as exc:
        record.status = "partial"
        record.stage = "partial"
        record.failed_stage = current_stage
        record.stage_durations[current_stage] = round(time.monotonic() - stage_started, 3)
        record.errors.append(str(exc))
    except Exception as exc:
        record.status = "failed"
        record.stage = "failed"
        record.failed_stage = current_stage
        record.stage_durations[current_stage] = round(time.monotonic() - stage_started, 3)
        record.errors.append(f"{type(exc).__name__}: {str(exc)[:500]}")
    finally:
        if model:
            record.usage = model.usage.copy()
            record.model = getattr(model, "actual_model", None) or record.model
        if not record.demo:
            record.model_calls = store.list_calls(record.run_id)
            if model and hasattr(model, "active_seconds"):
                record.active_seconds = max(0, model.active_seconds - sum(c.elapsed_seconds for c in record.preparation_records))
        record.finished_at = utcnow()
        store.save(record)
