"""Private definition-first scenario response; the public Forecast stays unchanged."""
from copy import deepcopy
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from .schemas import Claim, Forecast, ScenarioDetail, ScenarioForecast


class TerminalTarget(BaseModel):
    target: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    scope: str = Field(min_length=1)


class TerminalDefinition(BaseModel):
    outcome_id: Literal["outcome_1", "outcome_2", "outcome_3"]
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    conditions: list[str] = Field(min_length=1)


class TerminalWeight(BaseModel):
    outcome_id: Literal["outcome_1", "outcome_2", "outcome_3"]
    weight: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    simulation_ids: list[str] = Field(default_factory=list)


class DefinitionFirstForecast(BaseModel):
    # JSON property order deliberately places all definitions before any weight.
    terminal_target: TerminalTarget
    terminal_axis: str = Field(min_length=1)
    terminal_definitions: list[TerminalDefinition] = Field(min_length=2, max_length=3)
    terminal_weights: list[TerminalWeight] = Field(min_length=2, max_length=3)
    conclusion: str
    supporting: list[Claim] = Field(default_factory=list)
    opposing: list[Claim] = Field(default_factory=list)
    key_assumptions: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    new_information: list[str] = Field(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def accept_existing_public_candidate(cls, value, handler):
        # Existing adapters/demo fixtures can already return the public schema.
        # The model's advertised JSON schema remains definition-first. Preserve
        # old candidates verbatim; graph validation still rejects round outcomes.
        if isinstance(value, Forecast):
            return value.model_copy(deep=True)
        if isinstance(value, dict) and "probabilities" in value and "terminal_definitions" not in value:
            return ScenarioForecast.model_validate(value)
        return handler(value)


def to_public_forecast(candidate):
    if isinstance(candidate, Forecast):
        return candidate.model_copy(deep=True)
    definitions = candidate.terminal_definitions
    weights = candidate.terminal_weights
    ids = [item.outcome_id for item in definitions]
    weight_ids = [item.outcome_id for item in weights]
    names = [item.name for item in definitions]
    if len(set(ids)) != len(ids) or len(set(weight_ids)) != len(weight_ids) or set(ids) != set(weight_ids):
        raise ValueError("terminal_definitions与terminal_weights必须使用相同且不重复的outcome槽位")
    if len(set(names)) != len(names):
        raise ValueError("terminal_definitions的具名终态不能重复")
    by_id = {item.outcome_id:item for item in weights}
    details = [ScenarioDetail(name=item.name, definition=item.definition, conditions=item.conditions,
        rationale=by_id[item.outcome_id].rationale, evidence_ids=by_id[item.outcome_id].evidence_ids,
        assumption_ids=by_id[item.outcome_id].assumption_ids, simulation_ids=by_id[item.outcome_id].simulation_ids)
        for item in definitions]
    # Slot lookup only: never rescale, round, invent or reassign a probability.
    return Forecast(status="completed", probability_basis="full", conclusion=candidate.conclusion,
        probabilities={item.name:by_id[item.outcome_id].weight for item in definitions},
        supporting=candidate.supporting, opposing=candidate.opposing, key_assumptions=candidate.key_assumptions,
        scenarios=candidate.scenarios or [f"{item.name}：{item.definition}" for item in definitions],
        scenario_details=details, limitations=candidate.limitations, new_information=candidate.new_information)


WIRE_INSTRUCTIONS = (
    "先读report_first_read的审查限制，再按JSON顺序：terminal_target固定目标、同一目标日期和范围，terminal_axis固定一个可判断结果的轴；"
    "轴只选一种结果，不把‘X和Y的进展’两维捆绑。terminal_definitions先写2–3个具名终态，之后terminal_weights分配主观weight与rationale/E/H/S。"
    "按顺序划分：outcome_1先给可观察判定标准；outcome_2明写‘不满足outcome_1且…’；如有outcome_3，定义为不满足前两项的剩余情况。"
    "同一目标时点任何情况只能归入首个满足的终态；只用‘部分/有限’程度词不构成边界。conditions单列驱动条件，不能拿它代替终态定义。"
    "先写所有定义再分配权重，不把模拟轮次或各自条件成功率当终态；weight在0到1且合计1，数字未经校准。"
    "若terminal_task给定named_outcomes_from_question，沿用名称并补互斥边界。"
    "原文优先于F模型释义；disputed_interpretation与review.challenged_claim是被质疑释义，不能复制为无条件支持事实。"
    "来源只支持其原文的机构、领域和地区，不外推成其他机构/领域的已知事实；未提供规则不证明规则不存在。"
    "supporting/opposing只要引用H或S，整句用‘若…则可能…’明确条件；独立原文观察另写一条并只挂E。"
    "每条rationale先写‘原文实际记录…’，再写‘在H/S条件下可能…’，最后比较未决条件如何影响weight，不能把假设归因给E。"
    "模拟state_changes与unresolved冲突时保留未决条件，不把计划、愿望、同意写为已实现效果。"
    "只登记该条实际使用的E/H/S；模拟编号不是终态名。证据有限写限制，仍给未校准权重。")


def review_first_view(payload):
    """Qualify review-challenged interpretations in a private, quote-preserving view."""
    safe = deepcopy(payload)
    findings = {item.get("id"):item for item in (safe.get("evidence_assessment") or {}).get("findings", [])}
    constraints = []
    for issue in (safe.get("review") or {}).get("issues", []):
        affected = [fid for fid in issue.get("affected_ids", []) if fid in findings]
        if "claim" in issue:
            issue["challenged_claim"] = issue.pop("claim")
        if not affected:
            continue
        constraints.append({"finding_ids":affected, "review_limitation":issue.get("explanation", "")})
        for fid in affected:
            claim = findings[fid].get("claim", "")
            marker = "[disputed_interpretation：待检验释义，不可无条件断言] "
            if not claim.startswith(marker):
                findings[fid]["claim"] = marker + claim
    return {"report_first_read":{
        "rule":"以下是审查提出的限制，不是新增事实。回到exact quote，仅将原文直接表明的内容写作观察；其余保留条件。",
        "review_constraints":constraints,
        "definition_rule":"单一判定轴；按顺序排除先前终态，最后分支覆盖剩余情况；驱动条件与判定边界分开。"}, **safe}


def definition_first_task(question):
    outcomes = question.get("outcomes") or []
    return {
        "output_order":["terminal_target", "terminal_axis", "terminal_definitions", "terminal_weights", "report_text"],
        "named_outcomes_from_question":outcomes,
        "definition_example": {"note":"仅示例定义结构；实际目标、日期、范围和判定轴取自本问题，不复用示例项目",
            "axis":"假设道路项目在同一目标日期的完工状态",
            "terminal_definitions":[
                {"outcome_id":"outcome_1", "name":"全部完工", "definition":"计划道路全部验收完成"},
                {"outcome_id":"outcome_2", "name":"部分完工", "definition":"至少一段完成验收，但尚未全部验收"},
                {"outcome_id":"outcome_3", "name":"尚未完工", "definition":"没有路段完成验收"}]},
    }
