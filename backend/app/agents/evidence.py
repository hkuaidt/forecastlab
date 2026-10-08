"""Agent 2: source-backed findings, with one shared repair budget."""
from __future__ import annotations
import re
from ..schemas import (AssessmentCandidate, EvidenceAssessment, EvidenceFinding, RejectedFinding,
                       ConflictDetail, GapDetail, EvidencePassage)
from ..provenance import load_snapshot, save_snapshot, split_passages, select_passages, resolve_citation
from ..llm import BudgetExceeded
from ..evidence_quality import build_quality_profile
from ..cutoff_gaps import is_future_outcome_gap

PROMPT = """只依据提供的原文段落整理证据，不能用模型记忆补来源或结论。
每个finding必须有来源E编号、快照hash、段落编号和逐字原文quote。若存在活动前提P，可在target_premise_ids中指向相关P；
若没有活动前提，target_premise_ids必须为空，finding直接服务于研究问题，不得虚构P编号。
finding.claim 必须是所引 quote 可以直接蕴含的保守释义，只写 quote 本身明确表达的事实。
quote 没写出的发布日期、年份、机构/产品/项目名称、publisher/source title、文档或提交来源、问题中的用途、因果、趋势、评价、缺失事实或“因此/说明/构成/表明”的解释，
不得塞进 claim；需要说明局限时写进 limitation，不能把未核验事实移入 summary。即使这些信息出现在证据 metadata/title/publisher 里，只要 exact quote 没写，就不能补进 claim。
例如 quote 只有“The agency ... April 1”时 claim 可写“目标发射时间不早于4月1日”，不可补“NASA”；quote 只有“... on Jan. 16”时不可补年份；
quote 只有“Virtual Medal Table: United States 39 gold.”时不可补“Gracenote”。裸表格行只有日期和数值时，不可擅自补“收盘/指数/价格”等口径；
quote 只有简称/缩写时，不可补全成 quote 未出现的英文实体全名；quote 外的标题、章节名（如 Expected）、“官方”身份、固定提交 provenance 都不能进入 claim。
标题/截断片段只能按其字面内容生成 claim，不能从“没提到”推断“未发生”。
只提供了部分段落；没选中或没引用某主题不等于该来源没有相关内容，summary也不得作这种推断。
客户评价/Quote不是普遍性能测量：claim必须明确“该客户在所述工作流中反馈”，并保留任务/场景限制；
quote要包含该评价介绍使用场景的首句，不能只摘最后的性能数字。不能把发布时间当成实验完成日期。
多个 citations 只有在它们共同直接支持 claim 时才能合并到同一个 finding。
关系属于这个发现与前提，不属于整个网站；同一来源可以支持一项前提、挑战另一项。
不要因为检索任务叫challenge就把搜到的材料标成反证。没有可靠反证时不编造对立观点。
保留摘要/正文身份、日期未知、同源转载、未来计划等限制；计划不是实际发生的事实。
对前提为真与在该情景条件下讨论做区别，不将待核查前提当事实。
冲突用零起始finding_indexes关联双方，比较时间、指标和地区；口径不同不一定真矛盾。
缺口仅列信息截点前可能取得却未提供的资料；未来实际结果尚未发生不是证据缺口。
summary概括资料覆盖情况，不额外提出缺少引文的事实。不要输出概率。
输出要克制：优先保留最多8条对问题或活动前提最有信息量的finding；不要按来源机械生成一条finding，
多个来源重复表达同一事实时合并或只保留最直接、最权威的一项。conflicts最多3条，gaps最多4条；
每个finding优先1条直接引文，只有共同支持同一claim时才增加第2条citation。
不能引用未给出的段落，也不能把不相邻文字拼为一句引文；不输出字符偏移。"""


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
        if publisher and publisher in finding.claim and publisher not in quote_text:
            missing_publishers.append(publisher)
    metadata_missing_markers = (claim_markers & metadata_markers) - quote_markers
    inline_missing_markers = (claim_markers - quote_markers) if _CJK_TEXT.search(finding.claim) else set()
    missing_markers = sorted(metadata_missing_markers | inline_missing_markers)

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


def _compatibility(assessment, retrieval=None):
    if retrieval is not None:
        assessment.quality_profile = build_quality_profile(retrieval, assessment)
    assessment.conflicts = [f"{'已解释' if c.status == 'resolved' else '未解决'}：{c.issue}；{c.scope_comparison}；{c.explanation}" for c in assessment.conflict_details]
    assessment.gaps = [g.missing for g in assessment.gap_details]
    return assessment


def assess_evidence(question, framing, retrieval, model, data_dir) -> EvidenceAssessment:
    if retrieval.status == "failed":
        a = _compatibility(EvidenceAssessment(summary="取证全部失败，请检查检索配置或创建新运行。",
            retrieval_log=retrieval.retrieval_log, exclusions=retrieval.exclusions, gap_details=_source_gaps(retrieval)), retrieval)
        raise EvidenceStageError(retrieval, a)
    good_sources = []
    terms = [question.question, question.resolution_rule]
    if framing:
        terms += [p.content for p in framing.premises if p.user_review != "rejected"] + framing.alternative_directions
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
    payload = {"question": question.model_dump(mode="json"), "question_framing": active_framing(framing),
        "evidence": [e.model_dump(mode="json", exclude={"snapshot_path", "content_hash", "excerpt"}) for e in good_sources],
        "retrieval_log": [x.model_dump() for x in retrieval.retrieval_log]}
    candidate = None
    rejection_history = []
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
            if not rejected:
                break
            payload["validation_feedback"] = [r.reason for r in rejected]
        except BudgetExceeded as exc:
            if candidate is None:
                assessment.summary = "证据分析额度不足，已保存来源与检索日志。"
                raise EvidenceStageError(retrieval, _compatibility(assessment, retrieval)) from exc
            assessment.gap_details.append(GapDetail(missing="额度不足，未再修复无效发现", cause="validation_failed"))
            break
        except (ValueError, RuntimeError) as exc:
            rejection = RejectedFinding(candidate={}, reason=f"第{attempt+1}次结构输出无效：{str(exc)[:500]}")
            rejection_history.append(rejection)
            assessment.rejected_findings = list(rejection_history)
            payload["validation_feedback"] = rejection.reason
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
