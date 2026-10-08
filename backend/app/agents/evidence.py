"""Agent 2: source-backed findings, with one shared repair budget."""
from __future__ import annotations
import re
from ..schemas import (AssessmentCandidate, EvidenceAssessment, EvidenceFinding, RejectedFinding,
                       ConflictDetail, GapDetail, EvidencePassage)
from ..provenance import load_snapshot, save_snapshot, split_passages, select_passages, resolve_citation
from ..llm import BudgetExceeded, ModelCancelled
from ..evidence_quality import build_quality_profile
from ..cutoff_gaps import is_future_outcome_gap

PROMPT = """先识别用户问题的不同任务，从全部来源中为每个任务优先选择一条能回答具体事实问题的发现，再补真正改变判断的边界；材料不足的任务只写gap，不用其他任务的资料替代。
最多8条finding是上限，不是目标数量；不顺序逐源逐段总结，不为技术术语或同一机制的实现细节单列发现。同源中有不同决策价值的事实可以保留，不设每源硬配额。
只依据提供的原文段落，不用模型记忆补事实。先选择能独立支持一项具体判断的完整原文quote，再生成finding.claim。
每项必须给E编号、快照hash、paragraph_id和逐字quote；不能引用未给段落、拼接不相邻原文，不输出字符偏移。
finding.claim是quote可以直接蕴含的保守释义，只写quote 本身明确表达的事实；完整说明谁已做了什么、任务范围与结果。
实体、日期、数值、机构身份和因果均须由当前quote直接支持；标题/publisher等元数据不得塞进 claim。裸表格行不能补指标口径，固定提交信息不能补成原文事实。
主体仅在另一段时补直接citation或使用当前quote原称呼。AI/LLM可作通用类别词，具体模型或单项结果不能泛化为所有AI。
来源的预测、作者观点或未来展望须写成“该文认为/预计……”并保留条件，不能改写为当前现状、实际效果；计划不是已发生事件。
提供的是已保存来源的选段；保留body/snippet身份、日期未知、转载与历史回看限制。不能从“没提到”推断“未发生”，未选段落不等于来源不存在相关内容。
“没有完整原文”不能代替对已有内容的分析；有正文的来源不能说成只有摘要。summary只概括覆盖，不增加无引文事实。
客户评价/Quote须归属该客户及工作流，不冒充普遍测量；quote保留介绍场景的首句，不只截性能数字。
limitation只写该条具体的支持范围、缺失变量或对主体选择的限制；claim已明确且无具体缺口时可留空。
如需解释机制，这部分是AI解读而非原文事实，须用“若…则…”或“据此推测…”标明推断，不能新增未经证实的事件。
共性缺口只在gaps写一次，不给每条复制“资料未提供具体时间或细节，因此无法确定影响程度”。gap指明缺哪项可取得的记录以及影响哪个任务，未来实际结果尚未发生不是资料缺口。
clarification_answers是用户确认的研究范围，不是E证据或需要重查的P事实。finding和gap的target_premise_ids只从valid_target_premise_ids选；列表为空就填[]。Q是问题编号，不是前提；不虚构P。
relation描述发现与前提的关系，不描述整个网站；查询purpose=challenge不自动使结果成为反证，无反证不凑数。
冲突用零起始finding_indexes关联，比较时间、指标和地区；口径不同不必是真矛盾。conflicts最多3条，gaps最多4条，不输出概率。
每finding优先一条完整直接引文，仅在共同支持同一claim时增加第二条citation；保留必要原文，不为短输出删掉主语、条件或范围。"""


class EvidenceStageError(RuntimeError):
    def __init__(self, result, assessment):
        super().__init__(assessment.summary)
        self.result = result
        self.assessment = assessment


_MONTH_NUMBERS = {
    "january": "1", "jan": "1", "february": "2", "feb": "2", "march": "3", "mar": "3",
    "april": "4", "apr": "4", "may": "5", "june": "6", "jun": "6", "july": "7", "jul": "7",
    "august": "8", "aug": "8", "september": "9", "sep": "9", "sept": "9", "october": "10",
    "oct": "10", "november": "11", "nov": "11", "december": "12", "dec": "12",
}
_ASCII_CLAIM_MARKER = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9]*(?:[./-][A-Za-z0-9]+)*)(?![A-Za-z0-9_])"
)
_GENERIC_MODEL_CATEGORIES = {"ai", "llm", "llms"}
_CJK_TEXT = re.compile(r"[\u3400-\u9fff]")
_SEMANTIC_CUE_REQUIREMENTS = (
    ("收盘", ("收盘", " close ", " closed ", " closing ")),
    ("官方", ("官方", " official ")),
    ("固定提交", ("固定提交", " commit ", " committed ", " hash ")),
)


def _number_markers(text: str) -> set[str]:
    """Numbers/dates explicitly visible in text, with English month names normalized."""
    normalized = text.replace(",", "")
    markers = set(re.findall(r"\d+(?:\.\d+)?", normalized))
    lowered = text.casefold()
    for month, number in _MONTH_NUMBERS.items():
        if re.search(rf"\b{re.escape(month)}\.?\b", lowered):
            markers.add(number)
    return {m.lstrip("0") or "0" for m in markers}


def _ascii_claim_markers(text: str) -> set[str]:
    """Named ASCII entities/identifiers that should also be visible in the exact quote."""
    return {m.group(1).casefold() for m in _ASCII_CLAIM_MARKER.finditer(text)}


def _claim_boundary_violations(finding, citations, sources) -> list[str]:
    """Catch common metadata leakage that exact-quote validation alone cannot see."""
    quote_text = " ".join(c.quote for c in citations)
    claim_numbers = _number_markers(finding.claim)
    quote_numbers = _number_markers(quote_text)
    missing_numbers = sorted(claim_numbers - quote_numbers)

    claim_markers = _ascii_claim_markers(finding.claim)
    quote_markers = _ascii_claim_markers(quote_text)
    metadata_markers = set()
    missing_publishers = []
    for citation in citations:
        source = sources[citation.evidence_id]
        metadata_markers |= _ascii_claim_markers(" ".join(filter(None, [source.publisher, source.title])))
        publisher = (source.publisher or "").strip()
        if (publisher and publisher.casefold() not in _GENERIC_MODEL_CATEGORIES
                and publisher in finding.claim and publisher not in quote_text):
            missing_publishers.append(publisher)
    metadata_missing_markers = (claim_markers & metadata_markers) - quote_markers
    inline_missing_markers = (claim_markers - quote_markers) if _CJK_TEXT.search(finding.claim) else set()
    missing_markers = sorted((metadata_missing_markers | inline_missing_markers) - _GENERIC_MODEL_CATEGORIES)

    padded_quote = f" {quote_text.casefold()} "
    semantic_cues = []
    for cue, required_terms in _SEMANTIC_CUE_REQUIREMENTS:
        if cue in finding.claim and not any(term.casefold() in padded_quote for term in required_terms):
            semantic_cues.append(cue)

    issues = []
    if missing_numbers:
        issues.append("claim 含 exact quote 未出现的数字/年份/日期标记：" + "、".join(missing_numbers))
    if missing_markers:
        issues.append("claim 含 exact quote 未出现的英文实体/标签：" + "、".join(missing_markers))
    if semantic_cues:
        issues.append("claim 含 exact quote 未表达的语义口径/来源限定：" + "、".join(semantic_cues))
    if missing_publishers:
        issues.append("claim 把 publisher metadata 写成 quote 事实：" + "、".join(sorted(set(missing_publishers))))
    for citation in citations:
        source = sources[citation.evidence_id]
        paragraph = next((p.text.strip() for p in source.passages if p.paragraph_id == citation.paragraph_id), "")
        if re.match(r"^(?:Quote|Customer testimonial|客户评价|用户评价)\s*[“\"]", paragraph, re.I):
            introduction = re.sub(r"^(?:Quote|Customer testimonial|客户评价|用户评价)\s*[“\"]", "", paragraph, flags=re.I)
            first_sentence = re.split(r"(?<=[.!?。！？])\s+", introduction, maxsplit=1)[0]
            attributed = re.search(r"客户|用户|团队|作者|评价|反馈|customer|testimonial|team|user", finding.claim, re.I)
            scoped = re.search(r"(?:所述|该|其|此|这一).{0,12}(?:工作流|任务|场景|助手)|(?:their|this|that).{0,30}(?:workflow|task|assistant)", finding.claim, re.I)
            if first_sentence not in citation.quote or not attributed or not scoped:
                issues.append("客户评价须同时引用使用场景首句，并在claim保留客户归属和所述任务/工作流限定，不能泛化为普遍表现")
    return issues


def active_framing(framing):
    if framing is None:
        return None
    data = framing.model_dump(mode="json", exclude={"raw_question", "inputs", "analysis_record", "clarifications"})
    data["clarification_answers"] = [
        {"field": c.field, "question": c.question, "answer": c.answer}
        for c in framing.clarifications if c.status == "resolved" and c.answer is not None]
    active = {p.id for p in framing.premises if p.user_review != "rejected"}
    data["premises"] = [p for p in data["premises"] if p["id"] in active]
    data["retrieval_plan"] = [t for t in data["retrieval_plan"] if not t["target_premise_ids"] or any(p in active for p in t["target_premise_ids"])]
    for t in data["retrieval_plan"]:
        t["target_premise_ids"] = [p for p in t["target_premise_ids"] if p in active]
    return data


def validate_findings(candidate, framing, evidence, passages):
    sources = {e.id: e for e in evidence}
    active = {p.id for p in framing.premises if p.user_review != "rejected"} if framing else set()
    valid, rejected = [], []
    for index, finding in enumerate(candidate.findings):
        try:
            if set(finding.target_premise_ids) - active:
                raise ValueError("发现引用了不存在或被用户否认的前提")
            citations = []
            for citation in finding.citations:
                if citation.evidence_id not in sources:
                    raise ValueError("发现引用了不存在的证据编号")
                citations.append(resolve_citation(citation, sources[citation.evidence_id], passages.get(citation.evidence_id, [])))
            boundary_issues = _claim_boundary_violations(finding, citations, sources)
            if boundary_issues:
                raise ValueError("exact-quote claim boundary 越界：" + "；".join(boundary_issues) +
                                 "。删除 quote 外信息；可在 limitation 明确标为未核实，不得改写成 summary 中的事实。")
            valid.append(EvidenceFinding(id=f"F{index+1:03}", target_premise_ids=finding.target_premise_ids,
                claim=finding.claim, relation=finding.relation, citations=citations, limitation=finding.limitation))
        except ValueError as exc:
            rejected.append(RejectedFinding(candidate={"candidate_index": index, **finding.model_dump(mode="json")}, reason=str(exc)))
    return valid, rejected



def _future_information_gap(gap, question) -> bool:
    """Ignore a model-authored topic label unless its text confirms a future gap."""
    return is_future_outcome_gap(gap.missing, question, assume_missing=True)


def _details(candidate, findings, framing, logs, question):
    ids = {f.id for f in findings}
    active = {p.id for p in framing.premises if p.user_review != "rejected"} if framing else set()
    queries = {log.task_id for log in logs}
    conflicts, gaps, rejected = [], [], []
    for c in candidate.conflicts:
        references = [f"F{i+1:03}" for i in c.finding_indexes]
        if len(set(references)) < 2 or set(references) - ids:
            rejected.append(RejectedFinding(candidate={"conflict": c.model_dump()}, reason="冲突引用了无效或不足两项的发现"))
        else:
            conflicts.append(ConflictDetail(issue=c.issue, finding_ids=references, scope_comparison=c.scope_comparison,
                                             status=c.status, explanation=c.explanation))
    for gap in candidate.gaps:
        if _future_information_gap(gap, question):
            continue
        if set(gap.target_premise_ids) - active or set(gap.attempted_query_ids) - queries:
            rejected.append(RejectedFinding(candidate={"gap": gap.model_dump()}, reason="缺口引用未执行查询或无效前提"))
        else:
            gaps.append(gap)
    return conflicts, gaps, rejected


def _source_gaps(retrieval):
    gaps = []
    for log in retrieval.retrieval_log:
        if log.status == "failed":
            gaps.append(GapDetail(missing=f"检索任务 {log.task_id} 失败：{log.error}", attempted_query_ids=[log.task_id], cause="retrieval_failed"))
        elif log.status == "empty":
            gaps.append(GapDetail(missing=f"检索任务 {log.task_id} 未返回资料", attempted_query_ids=[log.task_id], cause="not_found"))
    unknown_dates = []
    for e in retrieval.evidence:
        if e.content_kind == "snippet":
            gaps.append(GapDetail(missing=f"{e.id} 只有搜索摘要，未取得正文", cause="snippet_only"))
        if e.published_at is None:
            unknown_dates.append(e.id)
        if e.availability == "historical_exercise":
            gaps.append(GapDetail(missing=f"{e.id} 为事后整理的历史练习，不是严格盲测快照", cause="historical_unverified"))
    if unknown_dates:
        preview = "、".join(unknown_dates[:5]) + (" 等" if len(unknown_dates) > 5 else "")
        gaps.append(GapDetail(missing=f"{len(unknown_dates)} 条来源缺少发布时间元数据（{preview}）；实时检索可记录取得时间，但不能据此证明历史截点可用性", cause="date_unknown"))
    for x in retrieval.exclusions:
        gaps.append(GapDetail(missing=f"来源排除：{x.get('source', '')}；{x.get('reason', '')}",
                              cause="after_cutoff" if "晚于" in x.get("reason", "") else "validation_failed"))
    return gaps


def _deduplicate_gaps(gaps):
    """Stable exact-meaning de-duplication; never merge different gap scopes."""
    result, seen = [], set()
    for gap in gaps:
        missing = re.sub(r"\s+", " ", gap.missing).strip()
        key = (missing, gap.cause, gap.topic,
               tuple(sorted(set(gap.target_premise_ids))), tuple(sorted(set(gap.attempted_query_ids))))
        if key not in seen:
            seen.add(key)
            result.append(gap.model_copy(update={"missing": missing}))
    return result


def _compatibility(assessment, retrieval=None):
    assessment.gap_details = _deduplicate_gaps(assessment.gap_details)
    if retrieval is not None:
        assessment.quality_profile = build_quality_profile(retrieval, assessment)
    assessment.conflicts = [f"{'已解释' if c.status == 'resolved' else '未解决'}：{c.issue}；{c.scope_comparison}；{c.explanation}" for c in assessment.conflict_details]
    # Display text may repeat across distinct structured scopes; keep those
    # scopes in gap_details while showing each exact normalized sentence once.
    assessment.gaps = list(dict.fromkeys(g.missing for g in assessment.gap_details))
    return assessment


def assess_evidence(question, framing, retrieval, model, data_dir, *, on_progress=None) -> EvidenceAssessment:
    if retrieval.status == "failed":
        a = _compatibility(EvidenceAssessment(summary="取证全部失败，请检查检索配置或创建新运行。",
            retrieval_log=retrieval.retrieval_log, exclusions=retrieval.exclusions, gap_details=_source_gaps(retrieval)), retrieval)
        raise EvidenceStageError(retrieval, a)
    good_sources = []
    terms = [question.question, question.resolution_rule]
    # Re-ranking must retain the precise terms that found the source, including
    # user-confirmed scope; these guide selection and do not become evidence.
    terms += [log.query for log in retrieval.retrieval_log]
    if framing:
        terms += [p.content for p in framing.premises if p.user_review != "rejected"] + framing.alternative_directions
        terms += [task.query for task in framing.retrieval_plan]
        terms += [c.answer for c in framing.clarifications if c.status == "resolved" and c.answer]
    for original in retrieval.evidence:
        e = original.model_copy(deep=True)
        try:
            if e.snapshot_hash:
                snapshot = load_snapshot(e, data_dir)
            else:
                snapshot = save_snapshot(e.excerpt, {"provider": "legacy-record", "title": e.title,
                    "legacy_retrieved_at": e.retrieved_at.isoformat()}, data_dir)
                e.snapshot_path, e.snapshot_hash = snapshot.snapshot_path, snapshot.snapshot_hash
                e.retrieved_at = snapshot.stored_at
                e.availability = "synthetic" if e.date_status == "synthetic" else "unverified"
            # Full snapshots remain server-side; Agent 2 sees only the most relevant bounded passages.
            # This keeps live-web pages from exhausting the structured-output budget.
            e.passages = select_passages(split_passages(snapshot), terms, limit=1400)
            good_sources.append(e)
        except (ValueError, OSError) as exc:
            retrieval.exclusions.append({"source": e.id, "reason": f"原文快照不可验证（{type(exc).__name__}）"})
    retrieval.evidence = good_sources
    assessment = EvidenceAssessment(summary="没有可核对来源。", evidence_ids=[e.id for e in good_sources],
        retrieval_log=retrieval.retrieval_log, exclusions=retrieval.exclusions, gap_details=_source_gaps(retrieval))
    if not good_sources:
        assessment.gap_details.append(GapDetail(missing="证据包中没有可读取的有效来源", cause="not_found"))
        return _compatibility(assessment, retrieval)
    passages = {e.id: e.passages for e in good_sources}
    valid_targets = [p.id for p in framing.premises if p.user_review != "rejected"] if framing else []
    target_feedback = (f"target_premise_ids只能来自valid_target_premise_ids={valid_targets!r}；"
                       "列表为空必须填[]。Q是问题编号，不是前提。")
    payload = {"question": question.model_dump(mode="json"), "question_framing": active_framing(framing),
        "valid_target_premise_ids": valid_targets,
        "evidence": [e.model_dump(mode="json", exclude={"snapshot_path", "content_hash", "excerpt"}) for e in good_sources],
        "retrieval_log": [x.model_dump() for x in retrieval.retrieval_log]}
    candidate = None
    rejection_history = []

    def publish_progress():
        if on_progress is not None:
            # A detached progress snapshot cannot mutate the next attempt, and
            # does not mark the evidence stage as completed.
            on_progress(_compatibility(assessment.model_copy(deep=True), retrieval))

    for attempt in range(2):
        try:
            candidate = model.complete("evidence12", payload, AssessmentCandidate, PROMPT, attempt_limit=1)
            findings, rejected = validate_findings(candidate, framing, good_sources, passages)
            conflicts, gaps, more_rejected = _details(candidate, findings, framing, retrieval.retrieval_log, question)
            rejected += more_rejected
            assessment.summary_audit.append(candidate.summary)
            assessment.findings, assessment.conflict_details = findings, conflicts
            assessment.findings_validated = True
            assessment.summary = verified_coverage_summary(assessment)
            assessment.gap_details = _source_gaps(retrieval) + gaps
            rejection_history.extend(rejected)
            assessment.rejected_findings = list(rejection_history)
            publish_progress()
            # Preserve usable, quote-checked findings instead of regenerating the
            # entire source package for unrelated rejected candidates.
            if findings or not rejected:
                break
            payload["validation_feedback"] = [target_feedback, *[r.reason for r in rejected]]
        except ModelCancelled:
            raise
        except BudgetExceeded as exc:
            if candidate is None:
                assessment.summary = "证据分析额度不足，已保存来源与检索日志。"
                publish_progress()
                raise EvidenceStageError(retrieval, _compatibility(assessment, retrieval)) from exc
            assessment.gap_details.append(GapDetail(missing="额度不足，未再修复无效发现", cause="validation_failed"))
            publish_progress()
            break
        except (ValueError, RuntimeError) as exc:
            rejection = RejectedFinding(candidate={}, reason=f"第{attempt+1}次结构输出无效：{str(exc)[:500]}")
            rejection_history.append(rejection)
            assessment.rejected_findings = list(rejection_history)
            if not assessment.findings_validated:
                assessment.summary = f"已保存{len(good_sources)}条来源；当前证据候选未通过校验，拒绝原因已记录。"
            publish_progress()
            payload["validation_feedback"] = target_feedback + " " + rejection.reason
    if not assessment.findings_validated:
        assessment.summary = f"已保存{len(good_sources)}条可读取来源，但证据分析输出未通过校验；不能据此认定来源没有相关内容。"
    if assessment.rejected_findings:
        assessment.gap_details.append(GapDetail(missing=f"已保留{len(assessment.rejected_findings)}条候选拒绝/结构错误审计，它们不是有效事实。", cause="validation_failed"))
    return _compatibility(assessment, retrieval)


def verified_coverage_summary(assessment) -> str:
    findings = assessment.findings if assessment.findings_validated else []
    cited = {citation.evidence_id for finding in findings for citation in finding.citations}
    return (f"保留{len(findings)}项带可定位原文引文的发现，引用{len(cited)}条来源。"
            "引文匹配不等于独立事实验证；未选中或未引用的段落不代表来源没有相关内容。")


def make_evidence_context(evidence, assessment, *, max_chars_per_source: int) -> dict:
    """Greedily keep whole findings; never retain a claim without its quoted text."""
    if max_chars_per_source < 0:
        raise ValueError("段落预算不能为负")
    lookup = {(e.id, p.paragraph_id): p for e in evidence for p in e.passages}
    ranges, kept, omitted = {}, [], []
    for finding in assessment.findings:
        proposed = dict(ranges)
        valid = True
        for c in finding.citations:
            key = (c.evidence_id, c.paragraph_id)
            p = lookup.get(key)
            if p is None or p.snapshot_hash != c.snapshot_hash or p.text[c.start-p.start:c.end-p.start] != c.quote:
                valid = False; break
            lo, hi = proposed.get(key, (c.start, c.end))
            proposed[key] = (min(lo, c.start), max(hi, c.end))
        totals = {}
        for (eid, _), (lo, hi) in proposed.items():
            totals[eid] = totals.get(eid, 0) + hi-lo
        if valid and all(n <= max_chars_per_source for n in totals.values()):
            kept.append(finding); ranges = proposed
        else:
            omitted.append(finding.id)
    output = []
    for e in evidence:
        selected = []
        source_ranges = [(key, value) for key, value in ranges.items() if key[0] == e.id]
        remaining = max_chars_per_source - sum(hi-lo for _, (lo, hi) in source_ranges)
        for key, (lo, hi) in sorted(source_ranges, key=lambda kv: kv[1][0]):
            p = lookup[key]
            before = min(80, lo-p.start, remaining); remaining -= before
            after = min(80, p.end-hi, remaining); remaining -= after
            start, end = lo-before, hi+after
            selected.append(EvidencePassage(paragraph_id=p.paragraph_id, start=start, end=end,
                text=p.text[start-p.start:end-p.start], snapshot_hash=p.snapshot_hash))
        used_ids = {p.paragraph_id for p in selected}
        for p in e.passages:
            if p.paragraph_id not in used_ids and len(p.text) <= remaining:
                selected.append(p); remaining -= len(p.text)
        data = e.model_dump(mode="json", exclude={"snapshot_path", "content_hash", "passages", "excerpt"})
        selected.sort(key=lambda p: p.start)
        data["passages"] = [p.model_dump() for p in selected]
        data["excerpt"] = "\n\n".join(p.text for p in selected)
        output.append(data)
    limitations = [f"上下文预算不足或段落不匹配，已省略发现 {fid}；不能据此认定该发现没有证据" for fid in omitted]
    visible = assessment.model_copy(deep=True)
    visible.findings, visible.rejected_findings = kept, []
    visible.summary_audit = []
    visible.summary = verified_coverage_summary(visible)
    ids = {f.id for f in kept}
    visible.conflict_details = [c for c in visible.conflict_details if set(c.finding_ids) <= ids]
    if omitted:
        visible.summary = f"本次模型上下文保留{len(kept)}项可追查发现，省略{len(omitted)}项。"
    _compatibility(visible)
    visible.gaps += limitations
    return {"evidence": output, "findings": kept, "assessment": visible, "limitations": limitations}
