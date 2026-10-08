"""Agent 1: produce a candidate, then apply deterministic ownership checks."""
from __future__ import annotations
import re
from uuid import uuid4
from pydantic import ValidationError
from ..schemas import (AnalyzeQuestionRequest, FramingCandidate, QuestionFraming, QuestionInput,
    QuestionDraft, QuestionSpec, QuestionClarification, QuestionPremise, RetrievalTask)

PROMPT = """澄清研究对象、时间、地区和判定标准，保留用户原意，不接受其预设结论。
premises 只识别用户文本中可被核查或证伪的背景事实、世界状态、因果解释或情景条件；
必须给 source_input_id 和逐字 original_span。中性问题可以完全没有 premise。
绝对不要把这些内容写成 premise：研究对象/实体名称、预测目标本身、信息截点、结算日期、比较日期、
大于/小于等比较规则、resolution_rule、resolution_source、binary/scenario 模式，或对问题原句的改写。
“既然/因为/由于/鉴于/假设”等前置陈述若是用户明确主张的事实，应作为 premise 提取并等待核查。
model_inferred 表示从措辞推断的世界状态或因果前提，绝不冒充用户明确观点；不要用它重新编码问题字段。
被用户否认的原有前提不得重新当作用户认可的前提。
把研究问题和用户的解释分开，给少量值得核查的 alternative_directions，不机械凑正反数量。
不要自己创造公司、地区、日期或成功阈值。已有 as_of、mode、resolve_by、resolution_rule、resolution_source
和 user_assumptions 都视为用户已经给定的问题定义；只要这些字段内部一致，就不要再次追问更细口径、
备用来源、格式偏好或常识性实体消歧。只有不回答就会导致两个合理解释产生不同结算结果时，才提出 blocking clarification；
非关键补充应设 blocking=false 或直接省略。已回答的澄清不要重复。
未来结果尚未发生是研究目标，不是让用户补充的事实。user_assumptions 只能照抄用户明确填写的条件。
最多3个检索任务，每个标注 purpose 和零起始 premise_indexes；方向可为 initial/challenge/alternative/background。
只输出 FramingCandidate，不生成草稿编号、状态、确认记录或外部证据。"""


def question_inputs(request: AnalyzeQuestionRequest, previous: QuestionFraming | None) -> list[QuestionInput]:
    inputs = [i.model_copy(deep=True) for i in previous.inputs] if previous else []
    def add(kind, text):
        inputs.append(QuestionInput(input_id=f"I{len(inputs)+1:03}", kind=kind, text=text))
    if not previous:
        if request.answers:
            raise ValueError("首次分析不能回答不存在的澄清问题")
        add("original", request.question.question)
    elif request.question.question != previous.proposed_spec.question:
        add("revision", request.question.question)
    known = {c.id for c in previous.clarifications} if previous else set()
    for answer in request.answers:
        if answer.clarification_id not in known:
            raise ValueError("澄清回答引用了不存在的问题编号")
        add("answer", answer.answer)
    previous_conditions = set(previous.proposed_spec.user_assumptions) if previous else set()
    for condition in request.question.user_assumptions:
        if condition not in previous_conditions:
            add("answer", condition)
    return inputs


def analyze_question(request: AnalyzeQuestionRequest, previous: QuestionFraming | None, model,
                     *, validation_feedback: str = "") -> FramingCandidate:
    inputs = question_inputs(request, previous)
    payload = {"question": request.question.model_dump(mode="json"),
               "inputs": [i.model_dump() for i in inputs],
               "previous_framing": previous.model_dump(mode="json") if previous else None,
               "answers": [a.model_dump() for a in request.answers],
               "validation_feedback": validation_feedback}
    # One transport attempt here; the service owns the single shared repair attempt.
    return model.complete("question12", payload, FramingCandidate, PROMPT, attempt_limit=1)


_SCOPE_RESTATEMENT = re.compile(
    r"^(?:(?:研究对象|研究问题|判定(?:对象|标准|条件|事件|时间|截止|口径)|"
    r"时间(?:截止|窗口|范围)|截止(?:时间|日期|时点)|比较(?:的)?(?:两个)?(?:时点|日期)|比较(?:对象|基准|方式)|"
    r"结果条件|结算(?:时间|日期|规则|来源)|数据来源|研究模式)\s*(?:是|为|[:：])\s*|"
    r"判定标准(?:包含|包括|要求)\s*)"
)
_QUESTION_RESTATEMENT = re.compile(r"^(?:该)?问题(?:关注|研究|为二元)")
_ENTITY_EXISTENCE = re.compile(
    r"^(?:存在(?:一个|一家|一支|一项|一场).*?(?:项目|公司|指数|峰会|大会|法案|任务|球队|赛事)|该研究对象存在)"
)


def _statement_text(text: str) -> str:
    return re.sub(r"\s+", "", text).rstrip("。.!！;；")


def question_spec_restatement(premise, spec: QuestionDraft | None = None) -> bool:
    """Reject recognizable schema restatements, not facts sharing a field's subject."""
    content = premise.content.strip()
    scope = _SCOPE_RESTATEMENT.search(content)
    if scope or _QUESTION_RESTATEMENT.search(content) or _ENTITY_EXISTENCE.search(content):
        # A literal user assertion such as "数据来源是伪造的" is not necessarily
        # metadata. Only discard it when it repeats an actual question-field value.
        statement = _statement_text(content)
        if premise.origin == "user_explicit" and statement in _statement_text(premise.original_span):
            values = (spec.question, spec.resolution_rule, spec.resolution_source, spec.mode,
                      spec.as_of.isoformat(), spec.resolve_by.isoformat() if spec.resolve_by else None) if spec else ()
            value = _statement_text(content[scope.end():]) if scope else statement
            return bool(value) and any(value == _statement_text(item) for item in values if item)
        return True
    if premise.origin == "model_inferred" and re.search(r"(?:二元(?:判定|问题|模式)|是/否|是否.*二元)", content):
        return True
    return False


def finalize_framing(candidate: FramingCandidate, request: AnalyzeQuestionRequest,
                     previous: QuestionFraming | None) -> QuestionFraming:
    inputs = question_inputs(request, previous)
    texts = {i.input_id: i.text for i in inputs}
    clarifications = [QuestionClarification(id=f"C{i+1:03}", **c.model_dump())
                      for i, c in enumerate(candidate.clarifications)
                      if c.field not in {"future_outcome", "actual_future_result"}]
    proposed = candidate.proposed_spec.model_copy(deep=True)
    # Explicit fields remain user-owned. Suggestions cannot silently overwrite them.
    for name in ("as_of", "mode", "resolve_by", "resolution_rule", "resolution_source", "user_assumptions"):
        supplied = getattr(request.question, name)
        fixed = name in {"as_of", "mode", "user_assumptions"} or supplied not in (None, "", [])
        if fixed and supplied != getattr(proposed, name):
            setattr(proposed, name, supplied)
            if not any(c.field == name for c in clarifications):
                clarifications.append(QuestionClarification(id=f"C{len(clarifications)+1:03}", field=name,
                    question=f"模型建议与您填写的 {name} 不同；目前保留您的值。需要更改请编辑后重新分析。", blocking=True))
    if request.question.mode == "scenario":
        # Scenario input is an explicit product choice, including absent settlement
        # fields. A model suggestion must not create a binary confirmation loop.
        scenario_fields = {"mode", "resolve_by", "resolution_rule", "resolution_source", "resolution", "outcomes"}
        for name in ("resolve_by", "resolution_rule", "resolution_source"):
            setattr(proposed, name, getattr(request.question, name))
        clarifications = [item for item in clarifications if item.field not in scenario_fields]
    if proposed.mode == "binary":
        for name, prompt in (("resolve_by", "请明确在哪个日期和时区判断结果。"),
                             ("resolution_rule", "什么可核对的情况算是，什么情况算否？")):
            if not getattr(proposed, name) and not any(c.field == name for c in clarifications):
                clarifications.append(QuestionClarification(id=f"C{len(clarifications)+1:03}", field=name, question=prompt))
    valid_spec = True
    try:
        QuestionSpec.model_validate(proposed.model_dump())
    except ValidationError:
        valid_spec = False
        if not clarifications:
            clarifications.append(QuestionClarification(id="C001", field="resolution", question="请核对结算时间晚于信息截点，且规则可以核查。"))
    old = previous.premises if previous else []
    next_number = max([previous.next_premise_number if previous else 1] + [int(p.id[1:])+1 for p in old])
    premises = []
    candidate_index_to_premise = {}
    for candidate_index, c in enumerate(candidate.premises):
        if c.source_input_id not in texts or c.original_span not in texts[c.source_input_id]:
            raise ValueError("候选前提的原话未出现在指定用户输入中")
        if question_spec_restatement(c, request.question):
            continue
        exact = next((p for p in old if (p.content, p.original_span, p.source_input_id, p.origin) ==
                      (c.content, c.original_span, c.source_input_id, c.origin)), None)
        if exact:
            premise = exact.model_copy(deep=True)
        else:
            replaced = next((p for p in old if p.id == c.replaces_id), None) if c.replaces_id else next(
                (p for p in old if p.source_input_id == c.source_input_id and p.original_span == c.original_span), None)
            if c.replaces_id and replaced is None:
                raise ValueError("前提替代关系引用不存在的编号")
            data = c.model_dump(); data["replaces_id"] = replaced.id if replaced else None
            premise = QuestionPremise(id=f"P{next_number:03}", **data)
            next_number += 1
        premises.append(premise)
        candidate_index_to_premise[candidate_index] = premise
    tasks = []
    for i, task in enumerate(candidate.retrieval_plan):
        if any(index < 0 or index >= len(candidate.premises) for index in task.premise_indexes):
            raise ValueError("检索任务引用了不存在的候选前提")
        targets = list(dict.fromkeys(candidate_index_to_premise[index].id for index in task.premise_indexes
                                     if index in candidate_index_to_premise))
        if task.premise_indexes and not targets:
            continue
        retained_targets = [p for p in targets if next(x for x in premises if x.id == p).user_review != "rejected"]
        if targets and not retained_targets:
            continue
        tasks.append(RetrievalTask(id=f"R{i+1:03}", query=task.query, purpose=task.purpose, target_premise_ids=retained_targets))
    return QuestionFraming(draft_id=request.draft_id or f"draft_{uuid4().hex}", revision=previous.revision+1 if previous else 1,
        raw_question=previous.raw_question if previous else request.question.question, proposed_spec=proposed,
        inputs=inputs, premises=premises, clarifications=clarifications, alternative_directions=candidate.alternative_directions,
        retrieval_plan=tasks, next_premise_number=next_number, demo_case_id=request.demo_case_id,
        status="ready_for_confirmation" if valid_spec and not any(c.blocking and c.status == "open" for c in clarifications) else "needs_clarification")
