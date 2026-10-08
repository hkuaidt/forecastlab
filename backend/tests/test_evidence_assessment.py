from copy import deepcopy
import importlib
import importlib.util
import pytest
from app.schemas import QuestionFraming, QuestionSpec, ImportedEvidence, AssessmentCandidate, utcnow
from app.sources import import_evidence


def module():
    assert importlib.util.find_spec("app.agents.evidence"), "premise-level evidence analysis not implemented"
    return importlib.import_module("app.agents.evidence")


def setup(tmp_path, clear_framing):
    q = QuestionSpec(question="青岚社区的测试是否足以支持按期发布？", mode="scenario", as_of=utcnow())
    framing = QuestionFraming.model_validate(clear_framing)
    framing.premises[0].user_review = "retained"
    p2 = framing.premises[0].model_copy(update={"id": "P002", "content": "兼容测试已完成"})
    framing.premises.append(p2)
    retrieval = import_evidence([ImportedEvidence(file_id="test-material", title="项目公告", excerpt=
        "发布方宣布单元测试完成，但兼容测试仍未通过。")], q, tmp_path)
    e = retrieval.evidence[0]; p = e.passages[0]
    citation = {"evidence_id": e.id, "snapshot_hash": e.snapshot_hash, "paragraph_id": p.paragraph_id, "quote": "单元测试完成"}
    good = {"summary": "公告说明不同测试阶段的状态。", "findings": [
        {"target_premise_ids": ["P001"], "claim": "公告表示单元测试已完成", "relation": "supports", "citations": [citation], "limitation": "不是全部测试"},
        {"target_premise_ids": ["P002"], "claim": "兼容测试仍未通过", "relation": "challenges",
         "citations": [{**citation, "quote": "兼容测试仍未通过"}], "limitation": "不能据此确定未来发布日期"}]}
    return q, framing, retrieval, good


@pytest.mark.parametrize("field,value", [("evidence_id", "E999"), ("snapshot_hash", "wrong"), ("paragraph_id", "never-provided"), ("quote", "原文没有这句话")])
def test_each_finding_requires_valid_quote_and_active_premise(tmp_path, clear_framing, field, value):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    good["findings"][0]["citations"][0][field] = value
    valid, rejected = m.validate_findings(AssessmentCandidate.model_validate(good), frame, retrieval.evidence,
        {e.id: e.passages for e in retrieval.evidence})
    assert len(valid) == 1 and len(rejected) == 1
    assert valid[0].target_premise_ids == ["P002"]


def test_rejected_premise_never_enters_valid_findings(tmp_path, clear_framing):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    frame.premises[0].user_review = "rejected"
    valid, rejected = m.validate_findings(AssessmentCandidate.model_validate(good), frame, retrieval.evidence,
        {e.id: e.passages for e in retrieval.evidence})
    assert len(rejected) == 1 and all("P001" not in f.target_premise_ids for f in valid)


def test_one_source_supports_and_challenges_different_premises(tmp_path, clear_framing, mock_model):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    model = mock_model([good]); assessment = m.assess_evidence(q, frame, retrieval, model, tmp_path)
    assert {f.relation for f in assessment.findings} == {"supports", "challenges"}
    assert all(f.citations for f in assessment.findings)
    assert model.calls[0][1]["question_framing"]["draft_id"] == frame.draft_id


def test_second_invalid_result_is_excluded_and_logged(tmp_path, clear_framing, mock_model):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    good["findings"][0]["citations"][0]["quote"] = "编造的引文"
    model = mock_model([good, good]); assessment = m.assess_evidence(q, frame, retrieval, model, tmp_path)
    assert len(assessment.findings) == 1
    assert assessment.rejected_findings[0].reason
    assert model.call_count == 2
    assert any(g.cause == "validation_failed" for g in assessment.gap_details)


def test_conflict_scope_differences_remain_visible(tmp_path, clear_framing, mock_model):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    good["conflicts"] = [{"issue": "测试状态不同", "finding_indexes": [0,1], "scope_comparison": "单元测试与兼容测试是不同口径",
        "status": "resolved", "explanation": "这两项不是同一事实的直接矛盾"}]
    a = m.assess_evidence(q, frame, retrieval, mock_model([good]), tmp_path)
    assert a.conflict_details[0].status == "resolved"
    assert "不同口径" in a.conflict_details[0].scope_comparison
    assert a.conflicts and "已解释" in a.conflicts[0]


def test_future_resolution_is_not_evidence_gap(tmp_path, clear_framing, mock_model):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    good["gaps"] = [{"missing": "未来是否最终发布的实际结果", "topic": "future_outcome", "cause": "not_found"}]
    a = m.assess_evidence(q, frame, retrieval, mock_model([good]), tmp_path)
    assert all("未来是否最终发布" not in g.missing for g in a.gap_details)


def test_downstream_context_never_keeps_orphaned_finding(tmp_path, clear_framing, mock_model):
    m = module(); q, frame, retrieval, good = setup(tmp_path, clear_framing)
    a = m.assess_evidence(q, frame, retrieval, mock_model([good]), tmp_path)
    original = a.model_dump()
    context = m.make_evidence_context(retrieval.evidence, a, max_chars_per_source=64)
    provided = {(e["id"], p["paragraph_id"]) for e in context["evidence"] for p in e["passages"]}
    assert all((c.evidence_id, c.paragraph_id) in provided for f in context["findings"] for c in f.citations)
    small = m.make_evidence_context(retrieval.evidence, a, max_chars_per_source=4)
    assert small["findings"] == [] and small["limitations"]
    assert a.model_dump() == original


def test_future_information_gap_detects_post_cutoff_market_result(clear_framing):
    from app.agents.evidence import _future_information_gap
    from app.schemas import GapDetail, QuestionSpec

    q = QuestionSpec.model_validate({
        "question": "科创50在2026-07-31是否高于基准？",
        "as_of": "2026-06-30T23:59:00+08:00",
        "resolve_by": "2026-07-31T23:59:00+08:00",
        "resolution_rule": "以官方收盘点位为准",
        "mode": "binary",
    })
    assert _future_information_gap(
        GapDetail(missing="缺少2026年7月31日的实际收盘点位", cause="not_found"), q
    ) is True
    assert _future_information_gap(
        GapDetail(missing="缺少截至2026年6月30日的指数成分权重", cause="not_found"), q
    ) is False


def test_evidence_prompt_requires_claim_to_be_directly_entailed():
    prompt = module().PROMPT
    assert "直接蕴含" in prompt
    assert "不能从“没提到”推断“未发生”" in prompt
    assert "不得塞进 claim" in prompt
    assert "裸表格行" in prompt
    assert "固定提交" in prompt


@pytest.mark.parametrize(
    "claim,quote,publisher",
    [
        (
            "2025-06-30 当日标普500与纳斯达克综合指数各上涨 0.5%，道琼斯工业平均指数上涨 0.6%。",
            "The S&P 500 (SPX) and Nasdaq Composite (IXIC) each rose 0.5%, while the Dow Jones Industrial Average (DJI) added 0.6%.",
            "Investopedia",
        ),
        (
            "在 2024/25 赛季赛程公布相关报道中，曼城被称为卫冕冠军。",
            "Who will champions Manchester City start their title defence against?",
            "Premier League",
        ),
        (
            "Python 3.13.0rc2 是最终发布预览，若无发现严重缺陷，该版本预计将成为最终的 3.13.0 正式版。",
            "This release, 3.13.0rc2, is the final release preview. This release is expected to become the final 3.13.0 release, barring any critical bugs being discovered.",
            "Python Software Foundation",
        ),
        (
            "InfoWorld 片段称，在一篇 12 月 2 日的博客文章中，微软提供了关于 TypeScript 7.0（又称 Project Corsa）的更新。",
            "In a December 2 blog post, Microsoft provided updates on TypeScript 7.0, also known as Project Corsa.",
            "InfoWorld",
        ),
        (
            "NASA 将此次试飞的目标发射时间定为不早于 4 月 1 日（星期三）。",
            "The agency is targeting no earlier than Wednesday, April 1, for the test flight.",
            "NASA",
        ),
        (
            "FAA 要求 SpaceX 就 2025 年 1 月 16 日发射操作中 Starship 飞行器的损失开展事故调查。",
            "The FAA is requiring SpaceX to perform a mishap investigation into the loss of the Starship vehicle during launch operations on Jan. 16.",
            "Federal Aviation Administration",
        ),
        (
            "在 PEP 719 的该固定提交中，3.13.0 candidate 2 被列为 2024-09-06（星期五）。",
            "- 3.13.0 candidate 2: Friday, 2024-09-06",
            "Python PEP Repository",
        ),
        (
            "Gracenote 虚拟奖牌榜预测美国获得 39 枚金牌。",
            "Virtual Medal Table: United States 39 gold.",
            "Nielsen",
        ),
        (
            "在 2026 年 4 月的 FIFA 男足世界排名中，法国居首，西班牙和阿根廷分列第二、第三。",
            "France now lead the way. Spain and Argentina are second and third respectively.",
            "FIFA",
        ),
    ],
)
def test_exact_quote_boundary_rejects_metadata_or_dates_not_in_quote(tmp_path, claim, quote, publisher):
    m = module()
    q = QuestionSpec(question="这些材料是否直接支持该事实？", mode="scenario", as_of=utcnow())
    retrieval = import_evidence(
        [ImportedEvidence(file_id="boundary-case", title=f"Boundary source: {claim}", publisher=publisher, excerpt=quote)],
        q,
        tmp_path,
    )
    e = retrieval.evidence[0]
    p = e.passages[0]
    candidate = AssessmentCandidate.model_validate({
        "summary": "boundary test",
        "findings": [{
            "claim": claim,
            "relation": "background",
            "citations": [{
                "evidence_id": e.id,
                "snapshot_hash": e.snapshot_hash,
                "paragraph_id": p.paragraph_id,
                "quote": quote,
            }],
        }],
    })
    valid, rejected = m.validate_findings(candidate, None, retrieval.evidence, {e.id: e.passages})
    assert valid == []
    assert len(rejected) == 1
    assert "exact-quote claim boundary" in rejected[0].reason


def test_exact_quote_boundary_allows_translated_month_when_quote_spells_month(tmp_path):
    m = module()
    q = QuestionSpec(question="目标发射日期是否被原文直接支持？", mode="scenario", as_of=utcnow())
    quote = "The agency is targeting no earlier than Wednesday, April 1, for the test flight."
    retrieval = import_evidence(
        [ImportedEvidence(file_id="boundary-month", title="Launch notice", publisher="NASA", excerpt=quote)],
        q,
        tmp_path,
    )
    e = retrieval.evidence[0]
    p = e.passages[0]
    candidate = AssessmentCandidate.model_validate({
        "summary": "boundary test",
        "findings": [{
            "claim": "目标发射时间定为不早于 4 月 1 日（星期三）。",
            "relation": "background",
            "citations": [{
                "evidence_id": e.id,
                "snapshot_hash": e.snapshot_hash,
                "paragraph_id": p.paragraph_id,
                "quote": quote,
            }],
        }],
    })
    valid, rejected = m.validate_findings(candidate, None, retrieval.evidence, {e.id: e.passages})
    assert len(valid) == 1
    assert rejected == []


def test_exact_quote_boundary_feedback_triggers_repair(tmp_path, clear_framing, mock_model):
    m = module()
    q, frame, retrieval, good = setup(tmp_path, clear_framing)
    bad = deepcopy(good)
    bad["findings"][0]["claim"] = "2026 年公告表示单元测试已完成"
    model = mock_model([bad, good])
    assessment = m.assess_evidence(q, frame, retrieval, model, tmp_path)
    assert model.call_count == 2
    assert len(assessment.findings) == 2
    assert assessment.rejected_findings == []
    assert "validation_feedback" in model.calls[1][1]
    assert any("exact-quote claim boundary" in x for x in model.calls[1][1]["validation_feedback"])


@pytest.mark.parametrize(
    "claim,quote,publisher,title",
    [
        (
            "2025-06-30 的收盘数值为 22679.010。",
            "2025-06-30\t22679.010",
            "FRED / Nasdaq, Inc.",
            "Table Data - NASDAQ-100",
        ),
        (
            "Cleveland Cavaliers 在周日与 OKC 一同进入60胜行列。",
            "The Cavs’ teamwork was on full display Sunday as they joined OKC in the 60-win club.",
            "NBA.com",
            "Starting 5, March 31",
        ),
        (
            "在固定提交中，3.13.0 final 被列为 Expected 2024-10-01（星期二）。",
            "- 3.13.0 final: Tuesday, 2024-10-01",
            "Python PEP Repository",
            "PEP 719 release schedule — fixed Git commit",
        ),
        (
            "官方曾公布将 TypeScript 编译器移植（port）的工作。",
            "This past March we unveiled our efforts to port the TypeScript compiler a",
            "Microsoft TypeScript Blog",
            "Announcing TypeScript Native Previews",
        ),
    ],
)
def test_exact_quote_boundary_v2_rejects_residual_semantic_expansion(tmp_path, claim, quote, publisher, title):
    m = module()
    q = QuestionSpec(question="这些材料是否直接支持该事实？", mode="scenario", as_of=utcnow())
    retrieval = import_evidence(
        [ImportedEvidence(file_id="boundary-v2", title=title, publisher=publisher, excerpt=quote)],
        q,
        tmp_path,
    )
    e = retrieval.evidence[0]
    p = e.passages[0]
    candidate = AssessmentCandidate.model_validate({
        "summary": "boundary v2 test",
        "findings": [{
            "claim": claim,
            "relation": "background",
            "citations": [{
                "evidence_id": e.id,
                "snapshot_hash": e.snapshot_hash,
                "paragraph_id": p.paragraph_id,
                "quote": quote,
            }],
        }],
    })
    valid, rejected = m.validate_findings(candidate, None, retrieval.evidence, {e.id: e.passages})
    assert valid == []
    assert len(rejected) == 1
    assert "exact-quote claim boundary" in rejected[0].reason


def test_exact_quote_boundary_v2_allows_english_entities_present_in_quote(tmp_path):
    m = module()
    q = QuestionSpec(question="这些材料是否直接支持该事实？", mode="scenario", as_of=utcnow())
    quote = "Apple will begin updating its Mac lineup with M4 chips in late 2024."
    retrieval = import_evidence(
        [ImportedEvidence(file_id="boundary-v2-positive", title="M4 update", publisher="Example", excerpt=quote)],
        q,
        tmp_path,
    )
    e = retrieval.evidence[0]
    p = e.passages[0]
    candidate = AssessmentCandidate.model_validate({
        "summary": "positive boundary test",
        "findings": [{
            "claim": "Apple 将于 2024 年末开始用 M4 芯片更新其 Mac 产品线。",
            "relation": "background",
            "citations": [{
                "evidence_id": e.id,
                "snapshot_hash": e.snapshot_hash,
                "paragraph_id": p.paragraph_id,
                "quote": quote,
            }],
        }],
    })
    valid, rejected = m.validate_findings(candidate, None, retrieval.evidence, {e.id: e.passages})
    assert len(valid) == 1
    assert rejected == []


def test_exact_quote_boundary_v2_allows_close_when_quote_says_closed(tmp_path):
    m = module()
    q = QuestionSpec(question="这些材料是否直接支持该事实？", mode="scenario", as_of=utcnow())
    quote = "The index closed at 22679.010."
    retrieval = import_evidence(
        [ImportedEvidence(file_id="boundary-v2-close", title="Index report", publisher="Example", excerpt=quote)],
        q,
        tmp_path,
    )
    e = retrieval.evidence[0]
    p = e.passages[0]
    candidate = AssessmentCandidate.model_validate({
        "summary": "positive close test",
        "findings": [{
            "claim": "该指数收盘为 22679.010。",
            "relation": "background",
            "citations": [{
                "evidence_id": e.id,
                "snapshot_hash": e.snapshot_hash,
                "paragraph_id": p.paragraph_id,
                "quote": quote,
            }],
        }],
    })
    valid, rejected = m.validate_findings(candidate, None, retrieval.evidence, {e.id: e.passages})
    assert len(valid) == 1
    assert rejected == []
