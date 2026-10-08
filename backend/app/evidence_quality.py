"""Conservative, deterministic evidence-coverage summary for the existing 证据评估 stage.

This module never verifies a source's independence, publisher, truthfulness, or
semantic entailment. It summarizes the already-validated run evidence and logs.
No retrieval, model calls, or independent API endpoint are added.
"""
from __future__ import annotations

from .schemas import EvidenceAssessment, EvidenceQualityProfile, RetrievalResult


def build_quality_profile(retrieval: RetrievalResult, assessment: EvidenceAssessment) -> EvidenceQualityProfile:
    sources = retrieval.evidence
    logs = retrieval.retrieval_log
    groups = {s.source_group or f"unclassified:{s.id}" for s in sources}
    snippet_only = sum(s.content_kind == "snippet" or s.source_type == "snippet_only" for s in sources)
    unknown_publication = sum(s.published_at is None for s in sources)
    truncated = sum(bool(s.content_truncated) for s in sources)
    suspected_reprint = sum(bool(s.possible_same_source) for s in sources)
    failed = sum(log.status == "failed" for log in logs)
    rejected = len(assessment.rejected_findings)
    unresolved = sum(c.status == "unresolved" for c in assessment.conflict_details)

    warnings: list[str] = []
    if not sources:
        warnings.append("本次没有可核对的有效来源。")
    elif len(groups) == 1:
        warnings.append("当前来源集中在一个来源组；来源组不等于已经核实的独立来源。")
    if snippet_only:
        warnings.append(f"{snippet_only} 条来源只有检索摘要，不能等同于已取得完整原文。")
    if unknown_publication:
        warnings.append(f"{unknown_publication} 条来源发布时间未知，取得时间不能证明历史截点前已可用。")
    if truncated:
        warnings.append(f"{truncated} 条来源正文被截断，相关引用不能视作完整上下文。")
    if suspected_reprint:
        warnings.append(f"{suspected_reprint} 条来源存在疑似同源/转载线索，不能简单计为独立佐证。")
    if failed:
        warnings.append(f"{failed} 个检索任务失败；应检查检索日志及覆盖缺口。")
    if retrieval.exclusions:
        warnings.append(f"{len(retrieval.exclusions)} 项候选或快照被排除；请检查排除原因。")
    if rejected:
        warnings.append(f"{rejected} 项证据发现/冲突/缺口候选未通过校验，不能作为有效事实。")
    if unresolved:
        warnings.append(f"{unresolved} 组发现级冲突尚未解决。")
    if any(s.availability == "historical_exercise" for s in sources):
        warnings.append("包含事后整理的历史练习资料，不应视为严格盲回测。")

    return EvidenceQualityProfile(
        source_count=len(sources),
        source_group_count=len(groups),
        body_source_count=sum(s.content_kind == "body" for s in sources),
        snippet_only_count=snippet_only,
        primary_label_count=sum(s.source_kind == "primary" or s.source_type == "primary" for s in sources),
        unknown_publication_count=unknown_publication,
        truncated_count=truncated,
        suspected_same_source_count=suspected_reprint,
        merged_alias_count=sum(max(0, len(s.aliases) - 1) for s in sources),
        search_success_count=sum(log.status == "success" for log in logs),
        search_empty_count=sum(log.status == "empty" for log in logs),
        search_failure_count=failed,
        excluded_count=len(retrieval.exclusions),
        validated_finding_count=len(assessment.findings) if assessment.findings_validated else 0,
        rejected_finding_count=rejected,
        unresolved_conflict_count=unresolved,
        warnings=warnings,
    )
