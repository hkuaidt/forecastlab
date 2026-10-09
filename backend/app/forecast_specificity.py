"""Concrete, future-facing forecast checks; does not certify model judgments."""
import re
from datetime import date
from .agents.evidence import explicit_dates

_VAGUE = re.compile(r"(?:显著|部分|有限|广泛|重大|明显|大幅).{0,4}(?:影响|提高|提升|改善|变化|采用)|无影响|影响有限|significant impact|partial impact|no impact", re.I)

def validate_specificity(report, question, *, horizon=None):
    errors = []
    predictions = report.predictions
    if not 3 <= len(predictions) <= 5:
        errors.append("详细报告必须包含3–5条具体预测predictions，而不只是程度标签")
    names = set(report.probabilities or {})
    if any(_VAGUE.search(name) for name in names):
        errors.append("情景名称过于笼统，请用主体实际做法或可观察结果命名，不能使用显著/部分/无影响")
    ids, actions, coverage = set(), set(), set()
    dates = explicit_dates(horizon or "") or explicit_dates(question.question)
    limit = question.resolve_by.date() if question.resolve_by else date.fromisoformat(max(dates)) if dates else None
    for item in predictions:
        if item.id in ids:
            errors.append("具体预测编号重复")
        ids.add(item.id)
        try:
            due = date.fromisoformat(item.by_date)
        except ValueError:
            errors.append(f"{item.id}观察截止日不是有效日期")
            continue
        if due <= question.as_of.date() or (limit and due > limit):
            errors.append(f"{item.id}观察截止日必须在未来，且不晚于目标时点")
        if not set(item.scenario_names) <= names:
            errors.append(f"{item.id}引用了不存在的情景名称")
        coverage.update(item.scenario_names)
        key = re.sub(r"\s+", "", item.actor + item.action)
        if key in actions:
            errors.append("具体预测重复同一主体行动，需区分工作环节或时间节点")
        actions.add(key)
        if not (item.evidence_ids or item.assumption_ids or item.simulation_ids):
            errors.append(f"{item.id}缺少可追溯的E/H/S依据")
        for field in ("observable_result", "verification", "falsifier"):
            value = getattr(item, field)
            if re.fullmatch(r"[\s。；，]*(?:有|产生|将有|预计)?(?:显著影响|部分影响|无影响|进一步验证|继续关注|具体情况待观察)[\s。；，]*", value):
                errors.append(f"{item.id}.{field}只有程度词或套话，没有可核对内容")
    if coverage != names:
        errors.append("每个情景至少关联一条具体预测，不能留下空泛分支")
    if errors:
        raise ValueError("；".join(dict.fromkeys(errors)))
