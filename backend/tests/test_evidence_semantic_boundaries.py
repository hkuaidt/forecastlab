"""Real-run regressions: menus, unsupported overview claims and client testimonials."""
from copy import deepcopy

import pytest
from app import graph
from app.agents.evidence import assess_evidence, make_evidence_context, _claim_boundary_violations
from app.provenance import save_snapshot, split_passages, select_passages
from app.schemas import (Evidence, EvidenceAssessment, FindingCandidate, FindingCitation, ImportedEvidence,
                         QuestionSpec, RunRecord)
from app.sources import import_evidence


NAV = "\n".join(["Skip to content", "Navigation Menu", "Sign in Appearance settings"] + [
    "Platform AI CODE CREATION GitHub Copilot Write better code with AI",
    "GitHub Copilot app Direct agents from issue to merge",
    "DEVELOPER WORKFLOWS Actions Automate any workflow", "APPLICATION SECURITY GitHub Advanced Security",
    "View all features", "Resources EXPLORE BY TOPIC AI"] * 12)
BODY = ("This repository contains mathematical manuscripts and supporting proof artifacts produced by an internal OpenAI model.\n"
        "This collection includes results at different stages of verification. Not all have accompanying Lean formalizations.\n"
        "The current catalogue contains 722 manuscripts organized into 372 families. A family groups related papers and alternative proofs.\n"
        "Many, but not all, of the manuscripts have been formalized. We will continue adding formalizations as we obtain them.")


def test_chinese_query_does_not_select_ai_navigation_instead_of_english_body(tmp_path):
    snapshot = save_snapshot(NAV + "\n" + BODY + "\n© 2026 GitHub, Inc.", {}, tmp_path)
    selected = select_passages(split_passages(snapshot), ["以2026年为信息截点，推演AI如何影响数学科研至2027年"], limit=1400)
    selected_text = "\n".join(p.text for p in selected)
    assert "mathematical manuscripts" in selected_text
    assert "Not all have accompanying Lean formalizations" in selected_text
    assert "722 manuscripts" in selected_text
    assert sum(len(p.text) for p in selected if p.start >= len(NAV)) > len(BODY) - 5
    for p in selected:
        assert snapshot.text[p.start:p.end] == p.text
        assert p.snapshot_hash == snapshot.snapshot_hash


def make_source(tmp_path, body="测试已经完成。", title="测试原文"):
    question = QuestionSpec(question="该项研究后续会如何发展？", mode="scenario")
    retrieval = import_evidence([ImportedEvidence(file_id="source", title=title, excerpt=body[:12000], body=body)], question, tmp_path)
    return question, retrieval


def candidate_from_source(source, *, claim=None, summary="该来源没有数学相关内容"):
    passage = source.passages[0]
    return {"summary": summary, "findings": [{"claim": claim or passage.text, "relation": "background", "citations": [{
        "evidence_id": source.id, "snapshot_hash": source.snapshot_hash, "paragraph_id": passage.paragraph_id, "quote": passage.text}]}]}


def test_model_summary_remains_audit_prose_and_cannot_reach_evidence_context(tmp_path, mock_model):
    question, retrieval = make_source(tmp_path)
    raw = candidate_from_source(retrieval.evidence[0])
    assessment = assess_evidence(question, None, retrieval, mock_model([raw]), tmp_path)
    assert assessment.summary_audit == [raw["summary"]]
    assert raw["summary"] not in assessment.summary
    context = make_evidence_context(retrieval.evidence, assessment, max_chars_per_source=900)
    assert context["assessment"].summary_audit == []
    assert raw["summary"] not in context["assessment"].summary
    assert "未选中或未引用" in context["assessment"].summary


def test_structural_retry_error_does_not_erase_earlier_rejected_candidate(tmp_path, mock_model):
    question, retrieval = make_source(tmp_path)
    raw = candidate_from_source(retrieval.evidence[0])
    bad = deepcopy(raw["findings"][0]); bad["claim"] = "测试完成，收入提升99%。"
    raw["findings"].append(bad)
    model = mock_model([raw, ValueError("缺少quote")])
    result = assess_evidence(question, None, retrieval, model, tmp_path)
    assert len(result.findings) == 1 and result.findings_validated
    assert len(result.rejected_findings) == 2
    assert "99" in result.rejected_findings[0].reason
    assert "缺少quote" in result.rejected_findings[1].reason
    assert result.summary_audit == [raw["summary"]]
    assert raw["summary"] not in result.summary


TESTIMONIAL = ('Quote “Viktor is an AI employee that lives in Slack and Microsoft Teams, so every step he takes shows up in our costs. '
               'At the same effort, Claude Opus 5.5 needs fewer steps and tool calls per task than Opus 5 and costs nearly half as much, '
               'while getting twice as many of our hardest tasks right.”')


@pytest.mark.parametrize("damage", ["truncated_quote", "unscoped_claim", "missing_attribution"])
def test_client_quote_cannot_be_promoted_to_general_model_performance(tmp_path, damage):
    question, retrieval = make_source(tmp_path, TESTIMONIAL)
    source = retrieval.evidence[0]; p = source.passages[0]
    quote = TESTIMONIAL
    claim = "该客户在所述助手工作流中反馈，Claude Opus 5.5的成本近半。"
    if damage == "truncated_quote": quote = 'Claude Opus 5.5 needs fewer steps and tool calls per task than Opus 5 and costs nearly half as much, while getting twice as many of our hardest tasks right.'
    if damage == "unscoped_claim": claim = "客户评价Claude Opus 5.5成本降低近半。"
    if damage == "missing_attribution": claim = "Claude Opus 5.5在该任务中成本降低近半。"
    finding = FindingCandidate(claim=claim, relation="background", citations=[{"evidence_id":source.id, "snapshot_hash":source.snapshot_hash,"paragraph_id":p.paragraph_id,"quote":quote}])
    assert any("客户评价" in x for x in _claim_boundary_violations(finding, finding.citations, {source.id:source}))


def test_scoped_attributed_client_evaluation_keeps_original_quote(tmp_path):
    question, retrieval = make_source(tmp_path, TESTIMONIAL)
    source = retrieval.evidence[0]; p = source.passages[0]
    finding = FindingCandidate(claim="该客户在所述助手工作流中反馈，Claude Opus 5.5的成本近半。", relation="background", citations=[{
        "evidence_id":source.id,"snapshot_hash":source.snapshot_hash,"paragraph_id":p.paragraph_id,"quote":TESTIMONIAL}])
    assert _claim_boundary_violations(finding, finding.citations, {source.id:source}) == []


def test_legacy_direct_downstream_receives_only_safe_summary(tmp_path, mock_model):
    question, retrieval = make_source(tmp_path)
    raw = candidate_from_source(retrieval.evidence[0])
    assessment = assess_evidence(question, None, retrieval, mock_model([raw]), tmp_path)
    # Also reproduce a previously saved unsafe summary, not just new records.
    assessment.summary = raw["summary"]
    record = RunRecord(run_id="summary_scope", question=question, evidence_mode="import", model="fixture", evidence=retrieval.evidence, evidence_assessment=assessment)
    model = mock_model([{"summary":"条件世界", "actors":[]}, {"status":"passed"}, {"status":"completed","conclusion":"待验证。","probabilities":{"推进":0.5,"受限":0.5}}])
    state = {"question":question.model_dump(mode="json"),"evidence":[e.model_dump(mode="json") for e in retrieval.evidence],"evidence_assessment":assessment.model_dump(mode="json")}
    graph.build_graph(record, retrieval.evidence, model, tmp_path, start_at="model_world").invoke(state)
    for role, payload, kwargs in model.calls:
        assert raw["summary"] not in str(payload)
        assert "summary_audit" not in payload.get("evidence_assessment", {})



@pytest.mark.parametrize("sentence", [
    "Enterprise customers improved code creation after deploying the new tool.",
    "The © symbol identifies the owner of this manuscript.",
    "Sign in failures were reduced after changing the authentication code.",
    "Available add-ons increase costs for the research workflow.",
])
def test_navigation_words_inside_real_prose_do_not_trigger_menu_penalty(sentence):
    from app.provenance import _navigation_label
    assert not _navigation_label(sentence)



def test_decimal_split_passages_are_selected_together_without_changing_old_ids(tmp_path):
    from app.schemas import EvidencePassage
    text = "The laboratory measured purity (96.4% versus 96.33%)."
    point = text.index("96.") + 3
    snapshot = save_snapshot(text, {}, tmp_path)
    passages = [EvidencePassage(paragraph_id="B000012", start=0, end=point, text=text[:point], snapshot_hash=snapshot.snapshot_hash),
                EvidencePassage(paragraph_id="B000013", start=point, end=len(text), text=text[point:], snapshot_hash=snapshot.snapshot_hash)]
    assert select_passages(passages, ["purity 4%"], limit=len(text)-1) == []
    selected = select_passages(passages, ["purity 4%"], limit=len(text))
    assert selected == passages
    assert "".join(p.text for p in selected) == text
