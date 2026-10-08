"""Temporal filters must not upgrade a refusal by deleting real evidence gaps."""
import pytest

from app.agents.evidence import _future_information_gap
from app.cutoff_gaps import is_future_outcome_gap
from app.graph import mistakes_future_outcome_for_missing_evidence, sanitize_evidence_audit
from app.schemas import Evidence, EvidenceOnlyAudit, GapDetail, QuestionSpec


@pytest.fixture
def question():
    return QuestionSpec(
        question="科创50在2026年10月31日收盘点位是否高于9月30日基准？",
        as_of="2026-09-30T23:59:00+08:00",
        resolve_by="2026-10-31T23:59:00+08:00",
        resolution_rule="10月31日官方收盘点位高于9月30日为是，否则为否。",
    )


@pytest.mark.parametrize("text", [
    "缺少信息截至日2026年9月30日的实际收盘价，无法核查比较基准。",
    "缺少2026-09-30的实际收盘点位",
    "缺少2026年9月29日的实际收盘价",
    "缺少上一交易日的实际收盘价",
    "缺少当日的实际收盘价",
    "缺少实际收盘价",
    "缺少上赛季最终排名",
    "缺少2025/26赛季最终积分榜",
    "缺少2026-09-30的收盘价和2026-10-31的收盘价",
    "缺少历史价格序列和未来实际结果",
    "缺少未来实际结果和基准价格",
    "缺少未来实际结果，同时没有可靠的数据来源",
    "缺少宏观经济数据、未来实际结果",
    "缺少未来结果且宏观经济数据不足",
    "缺少公司营收并缺少未来结果",
    "缺少未来结果并缺少公司营收",
    "缺少球队攻防数据来预测未来比赛结果",
    "缺少用于预测未来比赛结果的数据",
    "缺少对未来收盘价的预测依据",
    "缺少上一年结算日的实际结果",
    "缺少已结束赛事结算日的实际结果",
    "缺少最近结算日的实际结果",
    "缺少结算日之前的收盘价",
    "缺少2026年10月的赛程公告",
    "缺少未来结果的结算规则",
    "缺少2026年10月的预测数据",
    "缺少2026年13月31日的实际收盘价",
    "缺少2026年2月30日的实际收盘价",
    "缺少2026-10-100的实际收盘价",
])
def test_available_or_mixed_gaps_remain_blocking(question, text):
    assert not is_future_outcome_gap(text, question, assume_missing=True)
    assert not mistakes_future_outcome_for_missing_evidence(text, question, assume_missing=True)
    # A model-authored topic label cannot override concrete temporal content.
    for topic in ["available_information", "future_outcome"]:
        assert not _future_information_gap(GapDetail(missing=text, topic=topic), question)
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    can_estimate, reasons, discarded = sanitize_evidence_audit(audit, question, [])
    assert can_estimate is False
    assert reasons == [text]
    assert discarded == []


@pytest.mark.parametrize("text", [
    "缺少2026年10月31日的实际收盘点位",
    "缺少2026-10-31的收盘价",
    "缺少10月31日的实际收盘价",
    "缺少10月行情",
    "缺少未来实际结果",
    "未来是否最终发布的实际结果",
    "缺少结算日的实际收盘价",
    "缺少未来实际结果，因此无法预测",
    "缺少2026年10月31日的收盘价以及未来最终结果",
])
def test_confirmed_future_observations_are_filtered(question, text):
    assert is_future_outcome_gap(text, question, assume_missing=True)
    assert _future_information_gap(GapDetail(missing=text), question)
    assert mistakes_future_outcome_for_missing_evidence(text, question, assume_missing=True)
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    assert sanitize_evidence_audit(audit, question, []) == (True, [], [text])


def test_only_future_reason_is_removed_from_mixed_audit(question):
    future = "缺少2026年10月31日的实际收盘点位"
    baseline = "缺少2026年9月30日的实际收盘点位"
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[future, baseline])
    assert sanitize_evidence_audit(audit, question, []) == (False, [baseline], [future])


@pytest.fixture
def verified_evidence(question):
    return [Evidence(
        id="E001", title="冻结资料", file_id="cutoff-regression", excerpt="截点前资料原文。",
        retrieved_at=question.as_of, content_hash="a" * 64,
        source_type="exercise", source_group="cutoff-regression",
        availability="verified_before_cutoff",
    )]


@pytest.mark.parametrize("text", [
    "资料是历史练习，缺少截至日的基准收盘价",
    "资料为事后整理，关键的宏观经济数据缺失",
    "date_status=unknown，无法验证企业营收",
    "证据仅覆盖截点之前，关键数据缺失",
    "证据仅覆盖截至日及之前，尚未取得企业营收数据",
])
def test_verified_cutoff_does_not_erase_substantive_gaps(question, verified_evidence, text):
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    assert sanitize_evidence_audit(audit, question, verified_evidence) == (False, [text], [])


@pytest.mark.parametrize("text", ["资料为历史练习", "证据仅覆盖截至日及之前"])
def test_verified_cutoff_overrides_only_pure_metadata_objections(question, verified_evidence, text):
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    assert sanitize_evidence_audit(audit, question, verified_evidence) == (True, [], [text])


def test_non_missing_factual_context_does_not_hide_future_gap(question):
    assert is_future_outcome_gap("9月30日收盘为2200点，但没有10月行情，因而无法预测", question)
    assert not is_future_outcome_gap("预测期的实际结果尚待揭晓", question)


def test_current_season_resolution_is_tied_to_fixed_cutoff():
    question = QuestionSpec(
        question="阿森纳是否会在本赛季最终排名前四？",
        as_of="2026-09-30T23:59:00+08:00",
        resolve_by="2027-06-01T23:59:00+08:00",
        resolution_rule="最终积分榜前四为是，否则为否。",
    )
    for text in ["缺少2026/27赛季最终积分榜", "缺少赛季最终积分榜"]:
        assert _future_information_gap(GapDetail(missing=text), question)
        assert mistakes_future_outcome_for_missing_evidence(text, question, assume_missing=True)
    assert not _future_information_gap(GapDetail(missing="缺少上赛季最终积分榜"), question)
    assert not _future_information_gap(GapDetail(missing="缺少2025/26赛季最终积分榜"), question)


@pytest.fixture
def season_question():
    return QuestionSpec(
        question="阿森纳是否会在本赛季最终排名前四？",
        as_of="2026-10-08T23:59:00+08:00",
        resolve_by="2027-06-01T23:59:00+08:00",
        resolution_rule="2026/27赛季最终积分榜前四为是，否则为否。",
    )


@pytest.mark.parametrize("text", [
    "缺少2026/27赛季目前积分榜",
    "缺少2026/27赛季截至今日的球队进球数据",
    "缺少2026/27赛季积分榜",
    "缺少2026/27赛季迄今的比赛结果",
    "缺少2026/27赛季今天的排名数据",
    "缺少2026/27赛季第7轮最终排名",
    "缺少2026/27赛季阶段最终积分榜",
    "缺少2026/27赛季最终积分榜及目前积分榜",
    "缺少2025/26赛季最终积分榜",
])
def test_a_season_with_a_future_end_does_not_make_all_observations_future(season_question, text):
    assert not is_future_outcome_gap(text, season_question, assume_missing=True)
    for topic in ["available_information", "future_outcome"]:
        assert not _future_information_gap(GapDetail(missing=text, topic=topic), season_question)
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    assert sanitize_evidence_audit(audit, season_question, []) == (False, [text], [])


@pytest.mark.parametrize("text", [
    "缺少2026/27赛季最终积分榜",
    "缺少2026/27赛季最终排名",
    "缺少2026/27赛季结束时的结果",
    "缺少2026/27赛季未来比赛的实际结果",
])
def test_explicit_season_end_or_future_results_are_still_filtered(season_question, text):
    assert _future_information_gap(GapDetail(missing=text), season_question)
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    assert sanitize_evidence_audit(audit, season_question, []) == (True, [], [text])


def test_old_season_cannot_borrow_current_season_resolution_year():
    question = QuestionSpec(
        question="阿森纳是否会在本赛季最终排名前四？",
        as_of="2026-10-08T23:59:00+08:00",
        resolve_by="2026-12-31T23:59:00+08:00",
        resolution_rule="以最终积分榜为准。",
    )
    text = "缺少2025/26赛季最终积分榜"
    audit = EvidenceOnlyAudit(can_estimate=False, blocking_reasons=[text])
    assert sanitize_evidence_audit(audit, question, []) == (False, [text], [])
