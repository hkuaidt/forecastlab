"""Agent 1: produce a candidate, then apply deterministic ownership checks."""
from __future__ import annotations
import re
from uuid import uuid4
from pydantic import ValidationError
from ..schemas import (AnalyzeQuestionRequest, FramingCandidate, QuestionFraming, QuestionInput,
    QuestionDraft, QuestionSpec, QuestionClarification, QuestionPremise, RetrievalTask)

PROMPT = """产品只接受基于当前资料的未来预测，不接受历史回测或预测已经结束的事件。
as_of 是服务器记录的当前时间，不是可让用户选择的日期，不要要求用户修改它。
若用户预测目标已在 as_of 之前结束，必须提出 field=future_target、blocking=true 的澄清，要求改为未来目标；不能把过去目标悄悄改写成未来。
历史事实可以作为未来预测的背景，不应仅因为问题提及历史日期就拒绝。
澄清研究对象、时间、地区和判定标准，保留用户原意，不接受其预设结论。
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
非关键补充应设 blocking=false 或直接省略。已回答的澄清不要重复；answers中的回答和历史resolved问题由用户确认，不可重新要求回答同一问题。
未来结果尚未发生是研究目标，不是让用户补充的事实。user_assumptions 只能照抄用户明确填写的条件。
用户指定scenario时，情景定义/判定条件、主观概率及理由、公开证据、主要不确定性是后续研究应产出的内容，
不能反问用户先提供这些研究结果，也不能为这些输出要求建立blocking clarification。
“请分析/分别考虑/给出情景/明确条件和概率/列出证据”等任务指令不是世界事实，不能列入premises；
“将怎样影响/是否会发生”等问句也不预设其影响或结果已经成立。仅提取句中另外明确断言的可核查背景事实。
检索服务优先近90天、必要时扩大到一年。query查最新已发生的行动、发布、采用或否定结果，以便重建时间线；不要搜索未来预测年份，不要用旧综述代替当前状态。
retrieval_plan按以下结构生成，最多3条，每条query对应一个实际任务而非整个题目的综述：
1. 先按用户明确列出的研究任务或环节分配query；明确三个任务时各一条。主体或工具名不能替代任务覆盖。
2. 每条query聚焦一项实际任务或决策变量，结构为“具体研究术语 + 原始资料类型”；寻找已采取的行动与结果、实测边界或机构规则。
3. 全球技术生态的query使用通行英文术语和research paper、experiment、official documentation、policy等资料词；本地制度问题使用当地机构与语言。“现状/影响/未来发展趋势”不是资料类型。
4. 保留用户已回答范围中的工具名、任务名及其通行英文写法。purpose取initial/challenge/alternative/background，premise_indexes为零起始索引；没有事实前提时使用background。
数学三任务的短正例：“AI mathematical conjectures research paper”“Lean theorem prover official documentation”“journal generative AI peer review policy”。其他题目替换为自身任务，不能照搬数学分类。
alternative_directions写可比较的具体机制或利益冲突，只是待研究方向，不是新前提。只用问题中已有对象，不编造机构或事件。
前提示例：“关注甲团队、乙厂商：它们能采取什么行动，哪些条件下产生不同结果？”→premises=[]，这是范围和问句。
“甲团队已经发布工具，请比较各方行动”→只提取“甲团队已经发布工具”，不提取比较任务。
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



# Output instructions are work for the research pipeline, not claims about the world.
_TASK_INSTRUCTION = re.compile(
    r"^(?:(?:请|需要|要求|希望|务必|应当|必须|分别|同时|并|再|还|本研究(?:需要|要求)?)\s*)*"
    r"(?:给出|提供(?!方|者|商)|输出(?!结果|层|数据)|列出|列举|说明(?!书)|明确(?!的)|呈现|讨论|探讨|考虑(?!因素)|比较(?!基准|对象|结果|标准|数据)|评估(?!结果|报告)|推演(?!结果)|预测(?!结果|模型)|"
    r"分析(?!显示|表明|发现|提出|指出|证实|确认|结果)|研究(?!显示|表明|发现|提出|指出|证实|确认|证明|认为|团队|对象|已经|已))"
)
_SCENARIO_OUTPUT_FIELDS = {
    "判定条件", "判定标准", "情景判定条件", "情景条件", "情景定义", "情景", "场景", "概率", "概率依据", "概率评估依据",
    "公开证据", "主要不确定性", "不确定性", "输出格式", "分析方法",
    "scenarios", "scenario_definitions", "scenario_conditions", "probability", "probabilities",
    "probability_basis", "evidence", "uncertainty", "uncertainties", "output_format", "methodology",
}
_SCENARIO_OUTPUT_REQUEST = re.compile(
    r"(?:请|需要|请您).{0,16}(?:给出|提供|列出|说明|明确|确定|设定).{0,60}"
    r"(?:每个情景|各个情景|情景的(?:判定|条件|概率|定义)|场景的(?:条件|概率)|概率(?:评估|依据|分配|[，。？?]|$)|公开证据|主要不确定性)"
)
_QUESTION_FORM = re.compile(r"怎样|如何|是否|能否|会不会|何时|[？?]")
_ASSERTION_LEAD = re.compile(r"既然|因为|由于|鉴于|假设|已经|已(?:取得|完成|发布|发生|证明|宣布)|曾经")
_QUESTION_WORD = re.compile(r"怎样|如何|是否|能否|会不会|何时|什么|哪些|为何|谁")
_ACTOR_SCOPE_LIST = re.compile(r"^(?:请|重点|主要)?关注[^。！？?!：:]*、[^。！？?!：:]*[:：]$")
_SCOPE_ASSERTION = re.compile(r"增加|减少|增长|下降|上升|停止|完成|宣布|发布|通过|退出|倒闭|造假")


def _pure_research_question(text: str) -> bool:
    # Only discard an explicit question when every clause is interrogative.
    # A mixed sentence such as "预算增加20%，将如何调整？" retains its fact.
    if not text.endswith(("？", "?")) or _ASSERTION_LEAD.search(text):
        return False
    clauses = [part.strip() for part in re.split(r"[，,；;。：:]", text.rstrip("？?")) if part.strip()]
    return bool(clauses) and all(_QUESTION_WORD.search(part) for part in clauses)


def research_instruction_premise(premise) -> bool:
    content = premise.content.strip()
    span = premise.original_span.strip()
    if re.match(r"^(?:研究)?范围(?:限定|界定|设定)?(?:为|是|[:：])", span):
        return True
    if re.match(r"^(?:请)?(?:优先(?:检索|搜索|查找)|梳理时间线|正文无明确事件日期时使用文章发布日期)", content) and not _ASSERTION_LEAD.search(content):
        return True
    literal = _statement_text(content) in _statement_text(span)
    scope_parts = re.split(r"(?<=[：:])", span, maxsplit=1)
    scope, following = scope_parts[0], scope_parts[1].strip() if len(scope_parts) > 1 else ""
    if (_ACTOR_SCOPE_LIST.fullmatch(scope)
            and _statement_text(content) in {
                _statement_text(scope).rstrip("：:"), _statement_text(span)}
            and not _ASSERTION_LEAD.search(scope) and not _SCOPE_ASSERTION.search(scope)
            and (not following or _pure_research_question(following))):
        return True
    if (re.search(r"会怎样|将如何|会如何", content) and not _ASSERTION_LEAD.search(content)
            and not _SCOPE_ASSERTION.search(content)
            and not re.search(r"已经|曾经|已证明|证实|发现", content)):
        return True
    if (re.match(r"^(?:请|重点|主要)?关注", content) and "、" in content
            and re.search(r"具体行动|相互影响", content)
            and not _ASSERTION_LEAD.search(content) and not _SCOPE_ASSERTION.search(content)):
        return True
    if _pure_research_question(content):
        return True
    if literal:
        # "研究投入增加20%" is a factual assertion despite starting with a
        # word that can also be a verb. Only clear task/output requests override
        # the protection for an exact user statement.
        explicit_task = re.match(r"^(?:请|需要|要求|希望|务必|应当|必须|分别|本研究(?:需要|要求))", content)
        output_request = re.match(r"^(?:给出|列出|列举|输出|明确|说明)", content) and re.search(
            r"情景|场景|概率|判定条件|公开证据|主要不确定性", content)
        return bool(_TASK_INSTRUCTION.search(content) and (explicit_task or output_request))
    if _TASK_INSTRUCTION.search(content):
        return True
    if _TASK_INSTRUCTION.search(span):
        return True
    return bool(_QUESTION_FORM.search(span) and not _ASSERTION_LEAD.search(span))


def _output_only_query(query: str) -> bool:
    text = query.strip()
    text = re.sub(r"^(?:主要|未来|可能的)\s*", "", text)
    text = re.sub(r"^未来\s*", "", text)
    if re.fullmatch(r"(?:情景|场景|条件|主观概率|概率|证据局限|不确定性|及|与|和|[、，,\s])+", text):
        return True
    instruction = _TASK_INSTRUCTION.search(text)
    if instruction:
        text = text[instruction.end():].strip()
    return bool(re.search(
        r"^(?:[一二三四五六七八九十0-9]+[个种])?(?:互斥|覆盖主要可能性)|"
        r"^(?:情景|场景)(?:定义|判定条件|概率)|^(?:判定条件|概率|公开证据|主要不确定性)(?:[、，与和及\s]|$)", text))


def _background_query(question: str) -> str:
    # Remove only presentation instructions; keep the user's research subject.
    text = re.sub(r"^(?:请)?(?:推演|预测|展望)(?:至|到)[^：:？?]{1,30}[：:]", "", question.strip())
    for clause in re.split(r"[？?。；;]", text):
        clause = clause.strip()
        if not clause or _output_only_query(clause):
            continue
        return re.sub(r"怎样|如何|是否|能否", "", clause)[:400]
    return ""


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



def merge_clarification_answers(clarifications, request, previous):
    """User answers own resolution; candidate ordering never owns stable IDs."""
    old = previous.clarifications if previous else []
    identity = lambda c: (c.field.strip().casefold(), _statement_text(c.question).casefold())
    by_identity = {identity(c): c for c in old}
    answers = {a.clarification_id: a.answer for a in request.answers}
    next_number = max([0] + [int(c.id[1:]) for c in old if re.fullmatch(r"C[0-9]+", c.id)]) + 1
    result, seen = [], set()
    for candidate in clarifications:
        key = identity(candidate)
        if key in seen:
            continue
        seen.add(key)
        saved = by_identity.get(key)
        item = candidate.model_copy(deep=True)
        if saved:
            item.id = saved.id
            if saved.id in answers:
                item.answer, item.status = answers[saved.id], "resolved"
            elif saved.status == "resolved" and saved.answer is not None:
                item.answer, item.status = saved.answer, "resolved"
        else:
            item.id = f"C{next_number:03}"
            next_number += 1
        result.append(item)
    for saved in old:
        if identity(saved) in seen:
            continue
        if saved.id in answers or (saved.status == "resolved" and saved.answer is not None):
            item = saved.model_copy(deep=True)
            item.answer = answers.get(saved.id, saved.answer)
            item.status = "resolved"
            result.append(item)
    return result


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
        clarifications = [item for item in clarifications
            if item.field not in scenario_fields
            and item.field.strip().casefold() not in _SCENARIO_OUTPUT_FIELDS
            and not _SCENARIO_OUTPUT_REQUEST.search(item.question)]
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
    clarifications = merge_clarification_answers(clarifications, request, previous)
    old = previous.premises if previous else []
    next_number = max([previous.next_premise_number if previous else 1] + [int(p.id[1:])+1 for p in old])
    premises = []
    candidate_index_to_premise = {}
    instruction_indexes = set()
    for candidate_index, c in enumerate(candidate.premises):
        if c.source_input_id not in texts or c.original_span not in texts[c.source_input_id]:
            raise ValueError("候选前提的原话未出现在指定用户输入中")
        if question_spec_restatement(c, request.question):
            continue
        if research_instruction_premise(c):
            instruction_indexes.add(candidate_index)
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
        if _output_only_query(task.query):
            continue
        if task.premise_indexes and not targets and not set(task.premise_indexes) <= instruction_indexes:
            continue
        retained_targets = [p for p in targets if next(x for x in premises if x.id == p).user_review != "rejected"]
        if targets and not retained_targets:
            continue
        tasks.append(RetrievalTask(id=f"R{i+1:03}", query=task.query,
            purpose="background" if task.premise_indexes and not targets else task.purpose,
            target_premise_ids=retained_targets))
    if instruction_indexes and not tasks:
        query = _background_query(request.question.question)
        if query:
            tasks.append(RetrievalTask(id="R001", query=query, purpose="background"))
    return QuestionFraming(draft_id=request.draft_id or f"draft_{uuid4().hex}", revision=previous.revision+1 if previous else 1,
        raw_question=previous.raw_question if previous else request.question.question, proposed_spec=proposed,
        inputs=inputs, premises=premises, clarifications=clarifications, alternative_directions=candidate.alternative_directions,
        retrieval_plan=tasks, next_premise_number=next_number, demo_case_id=request.demo_case_id,
        status="ready_for_confirmation" if valid_spec and not any(c.blocking and c.status == "open" for c in clarifications) else "needs_clarification")
