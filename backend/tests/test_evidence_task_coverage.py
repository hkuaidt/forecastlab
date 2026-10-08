"""Task-prioritized evidence and exact gap de-duplication without losing scope."""
from app.agents.evidence import _compatibility, assess_evidence
from app.schemas import EvidenceAssessment, GapDetail, ImportedEvidence, QuestionSpec
from app.sources import import_evidence


def test_exact_gap_dedup_preserves_distinct_scope_and_reference_meaning():
    common = {"missing":"缺少  试点结果记录", "target_premise_ids":["P002", "P001"],
              "attempted_query_ids":["R002", "R001"]}
    first = GapDetail(**common)
    identical = first.model_copy(update={"missing":"  缺少\n试点结果记录  ",
        "target_premise_ids":["P001", "P002"], "attempted_query_ids":["R001", "R002"]})
    distinct = [first.model_copy(update=change) for change in [
        {"cause":"retrieval_failed"}, {"topic":"future_outcome"},
        {"target_premise_ids":["P003"]}, {"attempted_query_ids":["R003"]},
        {"missing":"缺少机构规则"}, {"missing":"没有试点结果记录"}]]
    assessment = EvidenceAssessment(summary="范围", gap_details=[first, identical, *distinct])
    result = _compatibility(assessment)
    assert len(result.gap_details) == 7
    assert result.gaps == ["缺少 试点结果记录", "缺少机构规则", "没有试点结果记录"]
    assert result.gap_details[0].target_premise_ids == ["P002", "P001"]
    assert result.gap_details[0].attempted_query_ids == ["R002", "R001"]
    assert result.gap_details[1].cause == "retrieval_failed"
    assert result.gap_details[2].topic == "future_outcome"
    assert result.gap_details[3].target_premise_ids == ["P003"]
    assert result.gap_details[4].attempted_query_ids == ["R003"]
    assert first.missing == common["missing"]


def test_repeated_model_gaps_are_deduplicated_in_progress_and_final_without_retry(tmp_path):
    question = QuestionSpec(question="试点执行与期刊采用会怎样影响后续路径？", mode="scenario")
    text = "试点已经完成。"
    retrieval = import_evidence([ImportedEvidence(file_id="task-coverage", title="试点记录",
        excerpt=text, body=text)], question, tmp_path)
    calls, progress = [], []
    missing = "未取得期刊采用规则。"
    class Model:
        def complete(self, role, payload, schema, instructions, **kwargs):
            calls.append(role)
            assert "从全部来源中为每个任务优先选择一条" in instructions
            assert "不为技术术语或同一机制的实现细节单列发现" in instructions
            assert "同源中有不同决策价值的事实可以保留，不设每源硬配额" in instructions
            assert "材料不足的任务只写gap，不用其他任务的资料替代" in instructions
            source = payload["evidence"][0]
            passage = source["passages"][0]
            return schema.model_validate({"summary":"覆盖试点，缺采用规则", "findings":[{
                "claim":text, "relation":"background", "citations":[{
                    "evidence_id":source["id"], "snapshot_hash":source["snapshot_hash"],
                    "paragraph_id":passage["paragraph_id"], "quote":passage["text"]}]}],
                "gaps":[{"missing":gap} for gap in [missing, "  " + missing, missing + "\n", missing]]})
    result = assess_evidence(question, None, retrieval, Model(), tmp_path, on_progress=progress.append)
    assert calls == ["evidence12"]
    assert len(result.findings) == 1 and result.findings_validated
    assert result.gaps.count(missing) == 1
    assert sum(g.missing == missing for g in result.gap_details) == 1
    assert progress[0].gaps.count(missing) == 1
