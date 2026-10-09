"""Private definition-first scenario response; the public Forecast stays unchanged."""
from copy import deepcopy
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from .schemas import Claim, Forecast, ScenarioDetail, ScenarioForecast, ConcretePrediction

WIRE_FORMAT = "definition-first-specific-v3"
_CONDITIONAL_PREFIX = "若所引用的H/S条件成立，则以下仅为条件性模型判断（未经核实）："


class TerminalTarget(BaseModel):
    target: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    scope: str = Field(min_length=1)


class TerminalDefinition(BaseModel):
    outcome_id: Literal["outcome_1", "outcome_2", "outcome_3"]
    name: str = Field(min_length=1, description="具体动作或可验收现实状态；禁用显著/部分影响、显著/部分提高等程度概括")
    definition: str = Field(min_length=1, description="有序判据：3槽时定义1=A、定义2=B、定义3仅剩余分支示例；2槽时定义1=A、定义2仅补集示例。")
    conditions: list[str] = Field(min_length=1)


class TerminalWeight(BaseModel):
    outcome_id: Literal["outcome_1", "outcome_2", "outcome_3"]
    weight: float = Field(ge=0, le=1, description="新生成的权重针对最终有序partition：3槽A/非A且B/非A且非B；2槽A/非A。不得沿用旧候选权重。")
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    simulation_ids: list[str] = Field(default_factory=list)


class ReferencedPrediction(ConcretePrediction):
    evidence_ids: list[str] = Field(description="实际使用的来源E编号；未使用时显式填[]")
    assumption_ids: list[str] = Field(description="实际使用的假设H编号；未使用时显式填[]")
    simulation_ids: list[str] = Field(description="实际使用的模拟S编号；未使用时显式填[]；三个依据列表不可全空")


class DefinitionFirstForecast(BaseModel):
    # JSON property order deliberately places all definitions before any weight.
    terminal_target: TerminalTarget
    terminal_axis: str = Field(min_length=1)
    terminal_definitions: list[TerminalDefinition] = Field(min_length=2, max_length=3)
    terminal_weights: list[TerminalWeight] = Field(min_length=2, max_length=3)
    predictions: list[ReferencedPrediction] = Field(min_length=3, max_length=5, description="必须3–5项可核对预测，含主体、行动、未来观察日、可观察结果、机制、验证材料、推翻信号与情景对应")
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
    if set(ids) != {f"outcome_{i+1}" for i in range(len(definitions))}:
        raise ValueError("有序终局必须使用连续outcome_1/outcome_2/可选outcome_3槽位")
    definitions = sorted(definitions, key=lambda item:item.outcome_id)
    by_id = {item.outcome_id:item for item in weights}
    target = candidate.terminal_target
    shared = f"目标时点：{target.horizon}；范围：{target.scope}；判定轴：{candidate.terminal_axis}。"
    a = definitions[0].definition
    interpreted = [shared + f"满足判据A：『{a}』。"]
    if len(definitions) == 3:
        b = definitions[1].definition
        interpreted.append(shared + f"不满足判据A『{a}』，且满足判据B『{b}』。")
        interpreted.append(shared + f"既不满足判据A『{a}』，也不满足判据B『{b}』的全部剩余情况。"
                           + f"模型对本分支的举例/倾向：{definitions[2].definition}（不构成额外必要条件）。")
    else:
        interpreted.append(shared + f"不满足判据A『{a}』的全部剩余情况。"
                           + f"模型对本分支的举例/倾向：{definitions[1].definition}（不构成额外必要条件）。")
    details = [ScenarioDetail(name=item.name, definition=interpreted[index], conditions=item.conditions,
        rationale="模型条件解释（依赖该分支条件；E仅提供背景，不证明以下完整结论）：" + by_id[item.outcome_id].rationale,
        evidence_ids=by_id[item.outcome_id].evidence_ids, assumption_ids=by_id[item.outcome_id].assumption_ids,
        simulation_ids=by_id[item.outcome_id].simulation_ids) for index,item in enumerate(definitions)]
    def conditional_claims(claims):
        result = []
        for claim in claims:
            public = claim.model_copy(deep=True)
            if public.assumption_ids or public.simulation_ids:
                public.text = _CONDITIONAL_PREFIX + public.text
            result.append(public)
        return result
    limitations = [*candidate.limitations,
        "终局使用预先声明的有序判定规则；互斥分组不代表判据和模型解释已被事实核真，主观权重未经校准。"]
    # Resolve unambiguous private slot references without inventing a scenario.
    names_by_slot = {item.outcome_id: item.name for item in definitions}
    predictions = [item.model_copy(update={"scenario_names": [names_by_slot.get(name, name) for name in item.scenario_names]}, deep=True)
                   for item in candidate.predictions]
    # Interpret the predeclared partition. Never rescale or reassign model weights.
    return Forecast(status="completed", probability_basis="full", conclusion=candidate.conclusion,
        probabilities={item.name:by_id[item.outcome_id].weight for item in definitions},
        supporting=conditional_claims(candidate.supporting), opposing=conditional_claims(candidate.opposing),
        key_assumptions=candidate.key_assumptions,
        scenarios=[f"{item.name}：{item.definition}" for item in details],
        scenario_details=details, predictions=predictions, limitations=limitations, new_information=candidate.new_information)


def qualify_model_claims(forecast):
    """Also cover real S references canonically recovered from a fresh wire's prose."""
    for claim in [*forecast.supporting, *forecast.opposing]:
        if (claim.assumption_ids or claim.simulation_ids) and not claim.text.startswith(_CONDITIONAL_PREFIX):
            claim.text = _CONDITIONAL_PREFIX + claim.text


WIRE_INSTRUCTIONS = (
    "任务是预测可观察变化，不能复述上游的开会、合作愿望。情景名及判据禁用显著/部分/无影响或提高等程度概括。"
    "先terminal_target固定同一目标日期和范围，terminal_axis固定一个可判断结果的轴，轴只选一种结果，再terminal_definitions写2–3个具名终态。"
    "使用有序partition：三槽A、非A且B、非A且非B；二槽A、非A。末槽定义仅为剩余分支举例，不增加必要条件。conditions单列驱动条件。"
    "先写所有定义再分配权重：terminal_weights针对最终partition新生成，合计1，数字未经校准；不可重用旧候选权重或将各自条件成功率归一化。"
    "S1/S2是同一轨迹先后状态，不是终态，不复制模拟摘要充当定义。给定有效具名outcomes时沿用并补边界。"
    "predictions优先3条，每个终态至少一条，包括未实现分支；不要重复动作凑5条。每条填K编号、主体、行动、未来by_date、可观察结果、若则机制、核验材料和推翻信号。"
    "日期晚于当前预测时间且不晚于目标；是观察期限，不是机构承诺。核验要说明检查产物中的哪个记录，不能只说看进展报告。"
    "scenario_names填实际名称或outcome槽位；三个E/H/S列表至少一个非空，只登记该条实际使用的E/H/S。阈值由模型提出时明确是未来判据，不假称实测。"
    "原文优先于F模型释义；原文只证明其直接记载，不跨主体/领域外推。review=blocked及disputed_interpretation保留质疑，不能把被质疑释义写成事实。"
    "supporting/opposing只要引用H或S时用若则条件语气；独立来源观察另写只挂E。每条rationale区分原文实际记录、在H/S条件下可能的机制、未决条件对权重的影响。"
    "模拟state_changes与unresolved冲突时保留未决条件，计划不等于成果；未提供规则不证明规则不存在。"
    "每个预测文本字段约20–60字，支持/反对各最多2条，局限不重复；只核对所选引句，保留来源日期及取证范围限制。")


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
        "version":WIRE_FORMAT,
        "output_order":["terminal_target", "terminal_axis", "terminal_definitions", "terminal_weights", "predictions", "report_text"],
        "specificity_example": {
            "note":"仅示范具体性，实际主体/产物/日期由本题资料推导，不照抄；未发生的判据都是模型假设。",
            "states":["交付物进入正式流程并附独立复核记录", "只发布可复现试点，尚未进入正式流程", "尚无可复现交付物，维持原有流程"],
            "positive_prediction":"负责交付的团队公开带版本号的产物及复现步骤，由独立团队逐项核对；看仓库、验收记录与实际采用流程是否对应。只有宣传或会议记录则不算实现。",
            "negative_prediction":"若复核成本持续超过资源预算，负责采用的团队继续逐项人工验收，仅把工具输出当候选；核对流程规则、人工签署记录。出现可复核的正式替代流程会推翻此判断。"},
        "specific_prediction_contract": {
            "count": "3–5条，所有情景均须有对应预测，不要重复主体+行动",
            "required_per_item": "K编号、actor、action、by_date、observable_result、mechanism、verification、falsifier、精确scenario_names、至少一个真实E/H/S引用",
            "quality": "写具体工作流/公开产物及验收方法，不把会议和合作本身当作科研效果；因果机制必须说明约束如何作用",
            "provenance": "每条填evidence_ids/assumption_ids/simulation_ids中至少一项；模拟计划用S，来源观察用E，不允许三项都为空"},
        "partition_contract":{
            "three_slots":["outcome_1: A=definition1", "outcome_2: NOT A AND B=definition2", "outcome_3: NOT A AND NOT B"],
            "two_slots":["outcome_1: A=definition1", "outcome_2: NOT A"],
            "last_definition":"仅为剩余分支的模型举例/倾向，不是额外必要条件",
            "weights":"针对以上最终partition新生成权重，保留模型数字；不重用旧候选权重",
            "conditions":"驱动条件独立保留，不添加到终局必要判据"},
        "named_outcomes_from_question":outcomes,
        "definition_example": {"note":"仅示例定义结构；实际目标、日期、范围和判定轴取自本问题，不复用示例项目",
            "axis":"假设道路项目在同一目标日期的完工状态",
            "terminal_definitions":[
                {"outcome_id":"outcome_1", "name":"全部完工", "definition":"计划道路全部验收完成"},
                {"outcome_id":"outcome_2", "name":"部分完工", "definition":"至少一段完成验收，但尚未全部验收"},
                {"outcome_id":"outcome_3", "name":"尚未完工", "definition":"没有路段完成验收"}]},
    }
