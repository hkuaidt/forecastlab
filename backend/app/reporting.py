"""Readable HTML export of the same public research results shown in the workspace."""
from html import escape
from pathlib import Path
from .provenance import load_snapshot
from .presentation import report_failed
from .schemas import RunRecord, utcnow


def report_html(run: RunRecord, data_dir: Path | None = None) -> str:
    esc = lambda value: escape(str(value))
    def para(text):
        return f"<p>{esc(text)}</p>" if text else ""
    def listing(items):
        return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>" if items else ""
    def refs(item):
        return "<small>" + " · ".join(f"{label}：{esc('、'.join(getattr(item, key, []) or []))}"
            for label, key in [("证据", "evidence_ids"), ("假设", "assumption_ids"), ("模拟", "simulation_ids")]
            if getattr(item, key, [])) + "</small>"
    def claims(items):
        return "".join(f"<article>{para(item.text)}{refs(item)}</article>" for item in items)
    def section(title, content):
        return f"<section><h2>{esc(title)}</h2>{content}</section>" if content else ""

    forecast = run.forecast
    status = "报告待修复" if report_failed(run) else {
        "completed": "已完成", "partial": "部分完成", "running": "运行中", "queued": "排队中",
        "cancelled": "已停止", "failed": "失败", "interrupted": "已中断", "scenario_only": "情景分析",
        "insufficient_evidence": "证据不足"}.get(run.status, run.status)
    metadata = para(f"运行 ID：{run.run_id} · 状态：{status} · 信息截至：{run.question.as_of.isoformat()}")
    metadata += para("问题来源：" + ("已确认的问题" if run.question_origin == "confirmed" else "旧版直接输入（未经过新版确认）"))
    if run.parent_run_id:
        metadata += para(f"父研究：{run.parent_run_id}")
    if run.report_repair_parent:
        metadata += para(f"本次为报告重新生成，沿用父研究上游结果；本次报告生成耗时 {run.active_seconds:.1f} 秒。")
    else:
        metadata += para(f"研究活动耗时 {run.active_seconds:.1f} 秒。")
    if run.question.mode == "binary":
        metadata += para(f"结算规则：{run.question.resolution_rule}")

    conclusion = para(forecast.conclusion if forecast else "研究尚未生成报告")
    if forecast and forecast.probabilities:
        details = {item.name: item for item in getattr(forecast, "scenario_details", [])}
        for name, probability in forecast.probabilities.items():
            item = details.get(name)
            conclusion += f"<article><h3>{esc(name)} <strong>{probability:.1%}</strong></h3>"
            if item:
                conclusion += para(item.definition) + listing(item.conditions) + para(item.rationale) + refs(item)
            elif run.question.mode == "scenario":
                conclusion += para("本条旧记录未保存具体情景定义及分配理由。")
            conclusion += "</article>"
        conclusion += para("模型主观概率，未经历史校准。已完成表示流程结束，证据局限和待核查问题见下文。")
    else:
        conclusion += para("无有效概率")
    body = section("结论与情景概率", conclusion)

    if forecast and forecast.predictions:
        content = para("以下是条件化预测；日期为观察截止点，模型设定的判据不代表来源已有承诺或实测结果。")
        for item in sorted(forecast.predictions, key=lambda p: p.by_date):
            content += f"<article><h3>{esc(item.actor)}：{esc(item.action)}</h3>"
            content += para(f"{item.id} · {item.by_date}前观察 · 对应情景：{' / '.join(item.scenario_names)}")
            for label, value in (("预期看到的具体变化", item.observable_result), ("为什么可能发生", item.mechanism),
                                 ("用什么材料核对", item.verification), ("什么会推翻预测", item.falsifier)):
                content += f"<h4>{label}</h4>" + para(value)
            content += refs(item) + "</article>"
        body += section("具体预测与验证节点", content)

    framing = run.question_framing
    if framing:
        body += section("问题与前提", para(framing.raw_question) + listing([
            f"{p.id} · {p.content} · {p.user_review} / {p.treatment}" for p in framing.premises]))
        body += section("已确认的澄清与研究范围", "".join(
            f"<article><h3>{esc(c.question)}</h3>{para(c.answer)}</article>"
            for c in framing.clarifications if c.status == "resolved" and c.answer))
        body += section("分析方向", listing(framing.alternative_directions))
    elif run.question_analysis:
        body += section("问题理解", para(run.question_analysis.normalized_question) + listing(run.question_analysis.caveats))
    assessment = run.evidence_assessment
    if assessment:
        quality = assessment.quality_profile
        overview = para(assessment.summary)
        if quality:
            overview += para(f"有效来源 {quality.source_count} · 正文 {quality.body_source_count} · 仅摘要 {quality.snippet_only_count} · 有效发现 {quality.validated_finding_count} · 被拒绝候选 {quality.rejected_finding_count}") + listing(quality.warnings)
        overview += "".join(f"<article><h3>{esc(f.id)} · {esc(f.claim)}</h3>{para(f.limitation)}" + "".join(
            f"<blockquote>{esc(c.quote)}</blockquote><small>{esc(c.evidence_id)} / {esc(c.paragraph_id)} / {c.start}–{c.end}</small>"
            for c in f.citations) + "</article>" for f in assessment.findings)
        overview += section("冲突和缺口", listing(assessment.conflicts + assessment.gaps))
        body += section("证据核查", overview)
    if run.world:
        world = run.world
        content = para(world.summary) + listing([f"{k}：{v}" for k, v in world.variables.items()])
        content += section("主体与目标", "".join(f"<article><h3>{esc(a.name)}</h3>{para(a.goal)}"
            + para("资源：" + "、".join(a.resources)) + para("约束：" + "、".join(a.constraints))
            + para("可见证据：" + "、".join(a.visible_evidence_ids)) + "</article>" for a in world.actors))
        content += section("主体关系", listing(world.relations))
        content += section("建模假设", "".join(f"<article><h3>{esc(a.id)} · {esc(a.content)}</h3>{para(a.rationale)}{para('依据：' + '、'.join(a.parent_ids))}</article>" for a in world.assumptions))
        body += section("世界建模", content)
    for step in run.simulation:
        actions = [a for a in run.actions if a.round == step.round]
        content = para(step.summary)
        for action in actions:
            actor = next((a.name for a in run.world.actors if a.id == action.actor_id), action.actor_id) if run.world else action.actor_id
            content += f"<article><h3>{esc(actor)} · {esc(action.action)}</h3>{para(action.rationale_summary)}"
            content += para("预期影响：" + action.expected_impact) + listing(action.conditions) + refs(action) + "</article>"
        content += section("状态变化", listing([f"{k}：{v}" for k, v in step.state_changes.items()]))
        content += section("冲突与未决事项", listing(step.conflicts + step.unresolved)) + refs(step)
        body += section(f"第 {step.round} 轮推演 · {step.id}", content)
    if forecast:
        body += section("支持依据", claims(forecast.supporting)) + section("反对依据", claims(forecast.opposing))
        body += section("关键假设", listing(forecast.key_assumptions))
        body += section("后续观察信号", listing(forecast.new_information))
    if run.review:
        review = run.review
        body += section("模型提出的待核查问题", para("下列意见属于模型审查提示，需结合原文判断，不等于已经证实的事实错误。")
            + "".join(f"<article><h3>{esc(i.claim)}</h3>{para(i.explanation)}{para('涉及：' + '、'.join(i.affected_ids))}</article>" for i in review.issues)
            + listing(review.unsupported_claims + review.missing_evidence))
    body += section("局限与运行记录", listing(list(dict.fromkeys((forecast.limitations if forecast else []) + run.errors))))
    if run.question.mode == "binary" and run.settlement:
        settled = run.settlement
        body += section("实际结果与评分", para(f"实际结果：{settled.outcome}") + para(settled.observed_value)
            + para(f"二元 Brier 分数：{settled.brier_score:.4f}" if settled.brier_score is not None else "无法计算 Brier 分数")
            + para(f"结算来源：{settled.source_url}"))
    sources = ""
    for evidence in run.evidence:
        text, label = evidence.excerpt, "证据摘录（可能截断；不是完整原文）"
        if data_dir:
            try:
                snapshot = load_snapshot(evidence, data_dir)
                text = snapshot.text
                label = ("搜索摘要快照（未取得网页正文）"
                         if evidence.content_kind == "snippet" or evidence.source_type == "snippet_only" else
                         "已保存原文快照（采集时已截断）" if snapshot.content_truncated else "完整已保存原文快照")
            except (ValueError, OSError):
                label = "证据摘录（原文快照不可用，未作为完整原文导出）"
        sources += f"<article id='{esc(evidence.id)}'><h3>{esc(evidence.id)} · {esc(evidence.title)}</h3>"
        sources += para(f"{evidence.publisher or ''} · 取证时间 {evidence.retrieved_at.isoformat()}") + para(label)
        sources += f"<blockquote>{esc(text)}</blockquote>" + (f"<a href='{esc(evidence.source_url)}'>查看来源</a>" if evidence.source_url else para("本地/教学材料")) + "</article>"
    body += section("证据原文与摘录", sources)
    lookback = " · 历史回看·非盲测" if any(e.source_type == "exercise" and e.retrieved_at > run.question.as_of for e in run.evidence) else ""
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ForecastLab 报告 {esc(run.run_id)}</title>
<style>body{{font:16px/1.8 system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 24px;color:#183438;overflow-wrap:anywhere}}h1,h2,h3{{line-height:1.4}}h2{{margin-top:40px}}small{{color:#607578}}article{{border-top:1px solid #d9e5e1;padding:16px 0}}blockquote{{background:#f1f6f4;padding:18px;margin:12px 0;white-space:pre-wrap}}p{{white-space:pre-wrap}}strong{{color:#0b776a}}@media print{{a{{color:inherit}}}}</style>
<p>FORECASTLAB · {esc('教学演示 / 虚构材料' if run.demo else '运行报告')}{lookback}</p><h1>{esc(run.question.question)}</h1>{metadata}{body}
<footer><small>导出于 {esc(utcnow().isoformat())}；证据、假设与模拟分别标注。</small></footer></html>"""
