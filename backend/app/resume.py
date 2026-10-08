"""Revalidate saved Agent 2 quotes when resuming records from before the trust flag."""
from copy import deepcopy
from pathlib import Path

from .agents.evidence import _claim_boundary_violations
from .evidence_quality import build_quality_profile
from .provenance import load_snapshot, split_passages, resolve_citation
from .schemas import CitationCandidate, Evidence, EvidenceAssessment, RetrievalResult, RunRecord


def restore_legacy_evidence_stage(record: RunRecord, data_dir: Path) -> bool:
    stage = record.stage_outputs.get("evidence")
    raw = stage.get("evidence_assessment") if isinstance(stage, dict) else None
    # An explicit False is a current rejection, not permission to certify model output.
    if not isinstance(raw, dict) or "findings_validated" in raw or not raw.get("findings"):
        return False
    assessment = EvidenceAssessment.model_validate(raw)
    evidence = [Evidence.model_validate(item) for item in stage.get("evidence", [])]
    sources = {item.id: item for item in evidence}
    try:
        if len(sources) != len(evidence):
            raise ValueError("证据编号重复")
        findings = {finding.id for finding in assessment.findings}
        if len(findings) != len(assessment.findings):
            raise ValueError("发现编号重复")
        if set(assessment.evidence_ids) - sources.keys():
            raise ValueError("评估引用不存在的来源")
        if any(set(conflict.finding_ids) - findings for conflict in assessment.conflict_details):
            raise ValueError("冲突引用不存在的发现")
        active = {p.id for p in record.question_framing.premises if p.user_review != "rejected"} if record.question_framing else set()
        paragraphs = {}
        for item in evidence:
            snapshot = load_snapshot(item, data_dir)
            full = {p.paragraph_id: p for p in split_passages(snapshot)}
            # Do not trust cached passage text, even when the stored snapshot is intact.
            for passage in item.passages:
                original = full.get(passage.paragraph_id)
                if original is None or original.model_dump() != passage.model_dump():
                    raise ValueError(f"{item.id} 保存的段落与原文不一致")
            paragraphs[item.id] = full
        for finding in assessment.findings:
            if set(finding.target_premise_ids) - active:
                raise ValueError("发现引用了不存在或被否认的前提")
            if not finding.citations:
                raise ValueError("发现没有原文引文")
            for citation in finding.citations:
                source = sources.get(citation.evidence_id)
                if source is None:
                    raise ValueError("引文引用不存在的来源")
                candidate = CitationCandidate.model_validate(citation.model_dump(exclude={"start", "end"}))
                restored = resolve_citation(candidate, source, list(paragraphs[source.id].values()))
                if restored.start != citation.start or restored.end != citation.end:
                    raise ValueError("引文偏移与保存的原文不一致")
                if not any(p.paragraph_id == citation.paragraph_id for p in source.passages):
                    source.passages.append(paragraphs[source.id][citation.paragraph_id])
            # An authentic quote cannot certify extra facts introduced by an old
            # model claim. Apply the same deterministic boundary checks as Agent 2.
            boundary_issues = _claim_boundary_violations(finding, finding.citations, sources)
            if boundary_issues:
                raise ValueError("exact-quote claim boundary 越界：" + "；".join(boundary_issues))
        for item in evidence:
            item.passages.sort(key=lambda passage: passage.start)
    except (ValueError, OSError) as exc:
        raise ValueError(
            f"旧版证据阶段重新核验失败：{exc}；已保留原记录，请重新导入原文创建运行。"
        ) from exc
    assessment.findings_validated = True
    retrieval = (record.retrieval_result.model_copy(deep=True) if record.retrieval_result else
                 RetrievalResult(retrieval_log=assessment.retrieval_log, exclusions=assessment.exclusions))
    retrieval.evidence = evidence
    assessment.quality_profile = build_quality_profile(retrieval, assessment)
    # Only replace the saved stage after every citation has passed validation.
    replacement = deepcopy(stage)
    replacement["evidence"] = [item.model_dump(mode="json") for item in evidence]
    replacement["evidence_assessment"] = assessment.model_dump(mode="json")
    record.stage_outputs["evidence"] = replacement
    record.evidence = evidence
    record.evidence_assessment = assessment
    record.retrieval_result = retrieval
    return True


def verify_saved_evidence_stage(record: RunRecord, data_dir: Path) -> None:
    """Locally recheck current caches too; do not mutate stages or promote trust."""
    stage = record.stage_outputs.get("evidence")
    if not isinstance(stage, dict):
        return
    raw = stage.get("evidence_assessment")
    evidence = [Evidence.model_validate(item) for item in stage.get("evidence", [])]
    if len({source.id for source in evidence}) != len(evidence):
        raise ValueError("恢复证据核验失败：证据编号重复")
    for source in evidence:
        try:
            snapshot = load_snapshot(source, data_dir)
            original = {p.paragraph_id: p for p in split_passages(snapshot)}
            for passage in source.passages:
                if original.get(passage.paragraph_id) != passage:
                    raise ValueError("保存段落与原文不一致")
        except (ValueError, OSError) as exc:
            raise ValueError(f"恢复证据核验失败：{source.id} {exc}") from exc
    if isinstance(raw, dict) and raw.get("findings") and raw.get("findings_validated") is True:
        private = record.model_copy(deep=True)
        private.stage_outputs["evidence"]["evidence_assessment"].pop("findings_validated")
        restore_legacy_evidence_stage(private, data_dir)
