"""Private definition-first scenario response; the public Forecast stays unchanged."""
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
    "按专用JSON顺序完成本次报告：先terminal_target固定目标、同一目标日期和范围，terminal_axis固定一个可判断结果的轴；"
    "再填terminal_definitions的2–3个具名终态，最后terminal_weights给对应槽位的主观weight与rationale/E/H/S依据。"
    "outcome_1/2/3仅为新终态槽；每条定义必须描述同一目标时点不同且互斥的结果状态，覆盖主要可能性。"
    "先写所有定义再分配权重，不能把模拟轮次或各自条件成功率当作终态。weight在0到1且合计1，数字未经校准。"
    "conditions是驱动条件；说明重叠时按什么终态边界判定。理由比较各终态为什么更可能或更不可能。"
    "若terminal_task给定named_outcomes_from_question，使用这些实际结果名称组织定义。"
    "原文优先于F模型释义；review指出缺直接依据的主张不能在conclusion写成既成事实。"
    "模拟state_changes与unresolved冲突时保留未决条件，不把计划、愿望、同意直接写为已实现效果。"
    "只登记该条实际使用的E/H/S；模拟编号是依据，不是终态名称。证据有限写限制，仍给未校准权重。")


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
