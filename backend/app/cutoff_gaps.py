"""Conservatively distinguish future observations from missing available evidence.

Dropping a gap can turn a refusal into a probability, so ambiguous or mixed
reasons must remain blocking. A model-supplied topic is not temporal evidence.
"""
from datetime import date
import re

from .schemas import QuestionSpec


_MISSING = re.compile(r"缺少|缺失|不足|尚未|未知|无法|不能|没有|未有|未发生|未提供|不具备")
_CLAUSES = re.compile(r"[，,、。；;！!？?\n]|以及|并且|同时|此外|但是|但|且|并|和|与|及|或")
_PURPOSE = re.compile(r"(?:以便|用于|用来|(?<!未)来|以|才能|才可)(?:预测|估计|评估|判断|研判)")
_CONSEQUENCE = re.compile(
    r"^(?:(?:因此|因而|所以|故|导致|从而)\s*)?"
    r"(?:无法|不能|不足以|不应)(?:进行|给出|作出)?"
    r"(?:预测|估计|判断|估算|提供概率|给出概率|输出概率|预测概率|概率估计|概率判断)[。\s]*$"
)
_AVAILABLE = re.compile(
    r"历史|过去|此前|先前|往年|去年|昨天|昨日|最近|之前|以前|"
    r"已(?:结束|完成|发生|公布|发布|确定)|上(?:一|个)?赛季|上季|"
    r"[上前](?:一|个)?(?:日|交易日|月|季度|年)|"
    r"当(?:日|天)|当前|目前|今日|今天|迄今|现阶段|本轮|第\d+轮|阶段|"
    r"基准|规则|口径|来源|渠道|可信|可靠|依据|支撑|支持|"
    r"伤病|伤停|阵容|财报|财务|计划|预计|预期|预测(?:值|数据)|"
    r"日程|赛程|公告|合约|权重|数据质量|方法|模型|"
    r"(?:截点|截至日|截止日)(?:前|之前|当日)|"
    r"(?:信息)?(?:截至|截止)日(?!之?后)"
)
_OBSERVATION = re.compile(r"实际|结果|最终|行情|收盘|点位|比分|进球|排名|积分榜|冠军|走势|数据")
_FUTURE = re.compile(r"未来|明(?:日|天)|后天|下(?:一|个)?(?:交易日|月|季度|赛季)|预测期|"
                     r"(?:截点|截至日|截止日)(?:之)?后|后续|结算(?:日|时|结果)|结果日")
_SEASON = re.compile(r"(?<!\d)(\d{4})\s*[/－–-]\s*(\d{2}|\d{4})\s*赛季")
_SEASON_FINAL = re.compile(
    r"赛季\s*(?:的)?(?:最终(?:的)?(?:结果|积分榜|排名|名次|冠军)|"
    r"结束(?:时|后)?(?:的)?(?:最终)?(?:结果|积分榜|排名|名次|进球数据)|冠军结果)"
)
_DATES = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*[年/-]\s*(?P<month>\d{1,2})"
    r"(?:\s*[月/-]\s*(?P<day>\d{1,2})\s*日?)?月?(?![\d/-])|"
    r"(?<![\d/-])(?P<short_month>\d{1,2})\s*月(?:\s*(?P<short_day>\d{1,2})\s*日?)?(?!\d)"
)


def _dates_after_cutoff(text: str, cutoff: date) -> bool | None:
    """True only when every explicit date/period starts after the cutoff."""
    matches = list(_DATES.finditer(text))
    if not matches:
        return None
    for match in matches:
        year = int(match["year"] or cutoff.year)
        month = int(match["month"] or match["short_month"])
        day = int(match["day"] or match["short_day"] or 1)
        try:
            if date(year, month, day) <= cutoff:
                return False
        except ValueError:
            return False
    return True


def _future_observation(text: str, question: QuestionSpec) -> bool:
    # The information being requested may be available now even when its
    # purpose is predicting a future result (e.g. team data to predict a match).
    text = _PURPOSE.split(text, maxsplit=1)[0]
    if _AVAILABLE.search(text) or not _OBSERVATION.search(text):
        return False

    # A season is a period, not a month/day (2026/27 must not be parsed as one).
    seasons = list(_SEASON.finditer(text))
    without_seasons = _SEASON.sub("赛季", text)
    dated = _dates_after_cutoff(without_seasons, question.as_of.date())
    if dated is False:
        return False
    if seasons:
        # The named season includes observations already available today.
        # Its future end year alone says nothing about an undated table/data.
        if not (_SEASON_FINAL.search(text) or dated is True or _FUTURE.search(text)):
            return False
        for match in seasons:
            start = int(match[1])
            end = int(match[2]) if len(match[2]) == 4 else start // 100 * 100 + int(match[2])
            if end <= start:
                return False
            if date(end, 1, 1) <= question.as_of.date():
                # In the end year, only a matching current-season forecast
                # provides enough context to identify the requested result.
                if not (question.resolve_by and question.resolve_by.year == end
                        and match[0] in question.question):
                    return False
        return True
    if dated is True or _FUTURE.search(text):
        return True

    # A bare closing price may be a baseline, so never infer its date. The
    # current season's final table can be tied to an explicit resolution rule.
    return bool(
        _SEASON_FINAL.search(text)
        and question.resolve_by
        and "本赛季" in question.question
        and re.search(r"最终(?:积分榜|排名|名次)", question.resolution_rule)
    )


def is_future_outcome_gap(text: str, question: QuestionSpec, *, assume_missing: bool = False) -> bool:
    """Whether all requested missing information is demonstrably future output.

    Keep a whole mixed reason rather than risk deleting an available-information
    gap. Factual context before the first missing-information clause is ignored;
    every subsequent substantive clause must independently be a future outcome.
    """
    if not assume_missing and not _MISSING.search(text):
        return False
    saw_gap = False
    missing_context = assume_missing
    for raw in _CLAUSES.split(text):
        clause = raw.strip()
        if not clause or _CONSEQUENCE.fullmatch(clause):
            continue
        if _MISSING.search(clause):
            missing_context = True
        if not missing_context:
            continue
        if not _future_observation(clause, question):
            return False
        saw_gap = True
    return saw_gap
