from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from threading import Lock
from uuid import uuid4
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import config
from .demo import DEMO_QUESTION, demo_evidence
from .graph import execute
from .schemas import QuestionDraft, QuestionSpec, RunRecord, RunRequest, Settlement, SettlementRequest, utcnow
from .sources import import_evidence
from .provenance import load_snapshot, split_passages
from .storage import RunStore, VersionConflict
from .question_service import QuestionService, ModelNotConfigured
from .report_repair import prepare_report_repair, report_repair_info
from .llm import BudgetExceeded
from .schemas import AnalyzeQuestionRequest, ConfirmQuestionRequest


def resolve_run_input(request: RunRequest, store: RunStore):
    if request.confirmation_id:
        confirmation = store.get_confirmation(request.confirmation_id)
        if not confirmation:
            raise KeyError("确认记录不存在")
        if bool(confirmation.demo_case_id) != (request.evidence_mode == "demo"):
            raise ValueError("真实确认不能使用教学证据，教学确认也不能使用真实取证模式")
        return (confirmation.question.model_copy(deep=True), confirmation.framing.model_copy(deep=True),
                [c.model_copy(deep=True) for c in store.list_calls(confirmation.draft_id) if c.phase == "preparation"])
    return request.question.model_copy(deep=True), None, []


def public_run_data(run: RunRecord):
    """Exports are audit records, not capabilities to read server files."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in {"snapshot_path", "declared_snapshot_path"}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean(run.model_dump(mode="json"))


def report_html(run: RunRecord) -> str:
    esc = lambda value: escape(str(value))
    claims = lambda rows: "".join(
        f"<li>{esc(c.text)} <small>{esc(', '.join(c.evidence_ids + c.assumption_ids + c.simulation_ids))}</small></li>" for c in rows
    )
    forecast = run.forecast
    origin = "已确认的问题" if run.question_origin == "confirmed" else "旧版直接输入（未经过新版确认）"
    framing_html = f"<h2>问题来源</h2><p>{esc(origin)}</p>"
    if run.question_framing:
        framing_html += f"<p>用户原话：{esc(run.question_framing.raw_question)}</p>"
        framing_html += "<ul>" + "".join(f"<li>{esc(p.id)} · {esc(p.content)} · {esc(p.user_review)} / {esc(p.treatment)}</li>" for p in run.question_framing.premises) + "</ul>"
    if run.evidence_assessment:
        quality = run.evidence_assessment.quality_profile
        if quality:
            framing_html += (
                "<h2>证据质量概览</h2>"
                f"<p>有效来源 {quality.source_count} · 来源组 {quality.source_group_count}"
                f"（未经独立性认证） · 正文 {quality.body_source_count} · 仅摘要 {quality.snippet_only_count}"
                f" · 有效发现 {quality.validated_finding_count} · 被拒绝候选 {quality.rejected_finding_count}</p>"
                "<p>来源标签、分组与快照哈希不证明来源真实、独立或语义蕴含。</p>"
                "<ul>" + "".join(f"<li>{esc(warning)}</li>" for warning in quality.warnings) + "</ul>"
            )
        framing_html += "<h2>逐项证据发现</h2>" + "".join(
            f"<article><h3>{esc(f.id)} · {esc(f.claim)}</h3><p>{esc(f.relation)} · 前提 {esc(', '.join(f.target_premise_ids))}</p>"
            + "".join(f"<blockquote>{esc(c.quote)}</blockquote><small>{esc(c.evidence_id)} / {esc(c.paragraph_id)} / {c.start}–{c.end}</small>" for c in f.citations)
            + f"<p>{esc(f.limitation)}</p></article>" for f in run.evidence_assessment.findings)
        framing_html += "<h3>冲突和缺口</h3><ul>" + "".join(f"<li>{esc(x)}</li>" for x in run.evidence_assessment.conflicts + run.evidence_assessment.gaps) + "</ul>"
    probability = "无有效概率" if not forecast or forecast.probabilities is None else " · ".join(f"{esc(k)} {v:.0%}" for k, v in forecast.probabilities.items())
    lookback_label = (" · 历史回看·非盲测" if any(
        e.source_type == "exercise" and e.retrieved_at > run.question.as_of for e in run.evidence
    ) else "")
    if run.settlement:
        settled = run.settlement
        score = (f"二元 Brier 分数：{settled.brier_score:.4f}（越低越好；单次结果不能证明概率已校准）"
                 if settled.brier_score is not None else "本次预测没有有效概率，无法计算 Brier 分数")
        settlement_html = (f"<p><strong>实际结果：{esc(settled.outcome)}</strong>"
                           f" · {esc(settled.observed_value or '未记录观测值')}</p>"
                           f"<p>{esc(score)}</p>"
                           f"<p>结算来源：<a href='{esc(settled.source_url)}'>{esc(settled.source_title or settled.source_url)}</a>"
                           f" · 记录于 {esc(settled.recorded_at.isoformat())}</p>"
                           + (f"<p>备注：{esc(settled.note)}</p>" if settled.note else ""))
    else:
        settlement_html = "<p>尚未记录实际结果。</p>"
    sources = "".join(
        f"<article id='{esc(e.id)}'><h3>{esc(e.id)} · {esc(e.title)}</h3><p>{esc(e.publisher or '')} · {esc(e.source_type)} · {esc(e.retrieved_at.isoformat())}</p>"
        f"<blockquote>{esc(e.excerpt)}</blockquote>" + (f"<p><a href='{esc(e.source_url)}'>查看来源</a></p>" if e.source_url else "<p>本地/教学材料</p>") + "</article>"
        for e in run.evidence
    )
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>ForecastLab 报告 {esc(run.run_id)}</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;max-width:850px;margin:48px auto;padding:0 24px;color:#183438}}h1,h2{{line-height:1.3}}small{{color:#607578}}article{{border-top:1px solid #d9e5e1;padding:14px 0}}blockquote{{background:#f1f6f4;padding:18px;margin:12px 0}}.tag{{color:#0b776a}}@media print{{a{{color:inherit}}}}</style>
<p class='tag'>FORECASTLAB · {esc('教学演示 / 虚构材料' if run.demo else '运行报告')}{lookback_label}</p><h1>{esc(run.question.question)}</h1>
<p>运行 ID：{esc(run.run_id)} · 状态：{esc(run.status)} · 信息截至：{esc(run.question.as_of.isoformat())} · 结算规则：{esc(run.question.resolution_rule)}</p>
{framing_html}<h2>结论</h2><p>{esc(forecast.conclusion if forecast else '运行未完成')}</p><p><strong>{probability}</strong> · 主观概率，未经校准</p>
<h2>实际结果与评分</h2>{settlement_html}
<h2>支持依据</h2><ul>{claims(forecast.supporting) if forecast else ''}</ul><h2>反对依据</h2><ul>{claims(forecast.opposing) if forecast else ''}</ul>
<h2>模拟与审查</h2><ol>{''.join('<li>'+esc(s.summary)+'</li>' for s in run.simulation)}</ol><p>{esc(', '.join(i.explanation for i in run.review.issues) if run.review else '无审查结果')}</p>
<h2>局限</h2><ul>{''.join('<li>'+esc(v)+'</li>' for v in (forecast.limitations if forecast else run.errors))}</ul><h2>证据原文</h2>{sources}
<footer><small>生成于 {esc(utcnow().isoformat())}；证据来源、假设和模拟记录分开保存。此报告不保证预测正确。</small></footer></html>"""


def create_app(data_dir: Path | None = None, *, question_model_factory=None) -> FastAPI:
    store = RunStore(data_dir or config.DATA_DIR)
    run_lock = Lock()
    settlement_lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.mark_interrupted()
        yield

    app = FastAPI(title="ForecastLab API", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    question_service = QuestionService(store, model_factory=question_model_factory)
    app.state.question_service = question_service

    def question_call(method, *args):
        try:
            return method(*args)
        except ModelNotConfigured as exc:
            raise HTTPException(503, str(exc)) from exc
        except VersionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except BudgetExceeded as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/questions/analyze")
    def analyze_question_endpoint(request: AnalyzeQuestionRequest):
        return question_call(question_service.analyze, request)

    @app.get("/api/questions/{draft_id}")
    def get_question_draft(draft_id: str):
        return question_call(question_service.get, draft_id)

    @app.post("/api/questions/{draft_id}/confirm")
    def confirm_question_endpoint(draft_id: str, request: ConfirmQuestionRequest):
        return question_call(question_service.confirm, draft_id, request)


    @app.get("/api/health")
    def health():
        return {"ok": True, "model_configured": bool(config.MODEL_API_KEY), "search_configured": bool(config.BRAVE_SEARCH_API_KEY or config.TAVILY_API_KEY), "model": config.MODEL_NAME}

    @app.get("/api/examples")
    def examples():
        from .agent12_demo import classroom_case
        return {"agent12_demo": classroom_case(), "demo": {"question": DEMO_QUESTION.model_dump(mode="json"), "evidence": [e.model_dump(mode="json") for e in demo_evidence()]},
                "presets": [
                    {"category": "科技", "question": "Python 3.15 是否会在 2026 年 11 月 15 日前发布正式版？", "resolve_by": "2026-11-15T23:59:00Z", "resolution_rule": "以 python.org 正式下载页出现 Python 3.15 正式版本为是，否则为否。", "resolution_source": "https://www.python.org/downloads/"},
                    {"category": "体育", "question": "阿森纳是否会在 2026/27 赛季英超最终排名前四？", "resolve_by": "2027-06-30T23:59:00Z", "resolution_rule": "以英超官网发布的 2026/27 赛季最终积分榜名次 1–4 为是，否则为否。", "resolution_source": "https://www.premierleague.com/tables"},
                    {"category": "公共事件", "question": "NASA Artemis III 是否会在 2027 年 12 月 31 日前完成载人近地轨道飞行测试？", "resolve_by": "2027-12-31T23:59:00Z", "resolution_rule": "以 NASA 官方任务公告确认 Artemis III 载人飞行测试完成为是，否则为否。", "resolution_source": "https://www.nasa.gov/mission/artemis-iii/"},
                ]}

    @app.post("/api/questions/parse")
    def parse_question(draft: QuestionDraft):
        missing = []
        if draft.mode == "binary":
            if not draft.resolve_by:
                missing.append("resolve_by")
            if not draft.resolution_rule.strip():
                missing.append("resolution_rule")
        if missing:
            return {"spec": None, "clarification_fields": missing}
        try:
            spec = QuestionSpec(**draft.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"spec": spec.model_dump(mode="json"), "clarification_fields": []}

    def enqueue(request: RunRequest, background_tasks: BackgroundTasks):
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "已有预测正在运行；请等待完成后再提交。")
        try:
            parent = store.get(request.parent_run_id) if request.parent_run_id else None
            if request.parent_run_id and not parent:
                raise HTTPException(404, "父运行不存在")
            if request.evidence_mode == "reuse" and parent and parent.demo:
                raise HTTPException(422, "教学虚构来源不能复用为真实运行的证据；请使用教学模式或真实证据包")
            question, framing, preparation = resolve_run_input(request, store)
            retrieval = None
            if request.evidence_mode == "demo":
                if framing:
                    from .agent12_demo import demo_materials
                    retrieval = demo_materials(question, framing, store.directory)
                    evidence = retrieval.evidence
                else:
                    question, evidence = DEMO_QUESTION, demo_evidence()
            else:
                if not config.MODEL_API_KEY:
                    raise HTTPException(503, "未配置 QWEN_API_KEY 或 DEEPSEEK_API_KEY；请先体验教学演示或配置后端密钥。")
                if request.evidence_mode == "import":
                    retrieval = import_evidence(request.evidence, question, store.directory)
                    evidence = retrieval.evidence
                elif request.evidence_mode == "reuse":
                    if not parent:
                        raise HTTPException(422, "沿用证据需要 parent_run_id")
                    if question.as_of < parent.question.as_of:
                        raise HTTPException(422, "沿用证据时，信息截至时间不能早于父运行")
                    evidence = [e.model_copy(deep=True) for e in parent.evidence]
                    from .schemas import RetrievalResult
                    retrieval = RetrievalResult(evidence=evidence)
                else:
                    if not (config.BRAVE_SEARCH_API_KEY or config.TAVILY_API_KEY):
                        raise HTTPException(503, "未配置 Brave 或 Tavily 搜索密钥；请导入证据包。")
                    evidence = []
            record = RunRecord(run_id=f"run_{uuid4().hex[:12]}", question=question,
                               parent_run_id=request.parent_run_id, question_version=2 if request.parent_run_id else 1,
                               evidence_mode=request.evidence_mode, demo=request.evidence_mode == "demo",
                               model="fixture" if request.evidence_mode == "demo" else config.MODEL_NAME,
                               confirmation_id=request.confirmation_id, question_framing=framing,
                               question_origin="confirmed" if framing else "legacy_direct",
                               preparation_records=preparation, retrieval_result=retrieval)
            record.question_version = framing.revision if framing else (parent.question_version+1 if parent else 1)
            store.save(record)
            def work():
                try:
                    execute(record, evidence, store)
                finally:
                    run_lock.release()
            background_tasks.add_task(work)
            return JSONResponse(status_code=202, content={"run_id": record.run_id, "status": "queued"})
        except VersionConflict as exc:
            run_lock.release()
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            run_lock.release()
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            run_lock.release()
            raise HTTPException(422, str(exc)) from exc
        except Exception:
            run_lock.release()
            raise

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest, background_tasks: BackgroundTasks):
        return enqueue(request, background_tasks)

    def run_view(record: RunRecord):
        # Reservations are durable before transport starts; expose them while a
        # stage is still running instead of waiting for its next snapshot.
        calls = store.list_calls(record.run_id)
        if calls:
            record.model_calls = calls
            record.usage = {
                "calls": max(record.usage["calls"], len(calls)),
                "prompt_tokens": max(record.usage["prompt_tokens"], sum(c.prompt_tokens for c in calls)),
                "completion_tokens": max(record.usage["completion_tokens"], sum(c.completion_tokens for c in calls)),
            }
        return {**record.model_dump(mode="json"), "report_repair": report_repair_info(record)}

    @app.get("/api/runs")
    def list_runs():
        return [run_view(r) for r in store.list()]

    @app.get("/api/settlements/summary")
    def settlement_summary():
        settled = [r.settlement for r in store.list(1_000_000)
                   if not r.demo and r.question.mode == "binary" and r.settlement is not None]
        scores = [item.brier_score for item in settled if item.brier_score is not None]
        return {"settled_count": len(settled), "scored_count": len(scores),
                "average_brier": round(sum(scores) / len(scores), 6) if scores else None,
                "reference_brier": 0.25}

    def required(run_id: str) -> RunRecord:
        run = store.get(run_id)
        if not run:
            raise HTTPException(404, "运行不存在")
        return run

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, background_tasks: BackgroundTasks):
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "已有预测正在运行；请等待完成后再重试。")
        try:
            record = required(run_id)
            if record.status not in {"failed", "partial", "interrupted"}:
                raise HTTPException(409, "只有失败、中断或部分完成的运行可以继续")
            if "forecast" in record.stage_outputs:
                raise HTTPException(409, "该运行已有完整报告")
            if not record.demo and not config.MODEL_API_KEY:
                raise HTTPException(503, "未配置模型密钥")

            def work():
                try:
                    execute(record, record.evidence, store, resume=True)
                finally:
                    run_lock.release()

            background_tasks.add_task(work)
            return JSONResponse(status_code=202, content={"run_id": record.run_id, "status": "queued"})
        except Exception:
            run_lock.release()
            raise

    @app.post("/api/runs/{run_id}/repair-report", status_code=202)
    def repair_report(run_id: str, background_tasks: BackgroundTasks):
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "已有预测正在运行；请等待完成后再重试。")
        try:
            parent = required(run_id)
            if not report_repair_info(parent)["available"]:
                raise HTTPException(409, "只有上游阶段完整的失败报告可以重新生成")
            if not config.MODEL_API_KEY:
                raise HTTPException(503, "未配置模型密钥")
            try:
                child = prepare_report_repair(parent, store.directory)
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(422, f"报告修复前的原文与轨迹核验未通过：{exc}") from exc
            store.save(child)
            def work():
                try:
                    execute(child, child.evidence, store, resume=True)
                finally:
                    run_lock.release()
            background_tasks.add_task(work)
            return JSONResponse(status_code=202, content={"run_id": child.run_id,
                "parent_run_id": parent.run_id, "status": "queued"})
        except Exception:
            run_lock.release()
            raise

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return run_view(required(run_id))

    @app.post("/api/runs/{run_id}/settlement", status_code=201)
    def settle_run(run_id: str, request: SettlementRequest):
        with settlement_lock:
            record = required(run_id)
            if record.demo or record.question.mode != "binary":
                raise HTTPException(422, "仅真实运行的二元预测可以结算")
            if record.settlement is not None:
                raise HTTPException(409, "该运行已记录实际结果")
            if record.question.resolve_by is None or utcnow() < record.question.resolve_by:
                raise HTTPException(409, "结算时间尚未到达")
            if record.forecast is None:
                raise HTTPException(409, "该运行尚无预测报告")
            if request.outcome not in record.question.outcomes:
                raise HTTPException(422, f"实际结果必须是：{'、'.join(record.question.outcomes)}")
            probabilities = record.forecast.probabilities
            score = None
            if probabilities is not None and set(probabilities) == set(record.question.outcomes):
                if all(0 <= value <= 1 for value in probabilities.values()) and abs(sum(probabilities.values()) - 1) <= 0.001:
                    score = round((1 - probabilities[request.outcome]) ** 2, 6)
            record.settlement = Settlement(**request.model_dump(mode="json"),
                                           forecast_probabilities=dict(probabilities) if probabilities is not None else None,
                                           brier_score=score)
            # Keep the original completed-stage snapshot intact; the SQLite record
            # adds the later observed outcome without changing the forecast.
            store.save(record, snapshot=False)
            return record

    @app.get("/api/runs/{run_id}/evidence")
    def get_evidence(run_id: str):
        return required(run_id).evidence

    @app.get("/api/runs/{run_id}/evidence-assessment")
    def get_evidence_assessment(run_id: str):
        return required(run_id).evidence_assessment

    @app.get("/api/runs/{run_id}/evidence/{evidence_id}/passages")
    def get_evidence_passages(run_id: str, evidence_id: str):
        evidence = next((e for e in required(run_id).evidence if e.id == evidence_id), None)
        if not evidence:
            raise HTTPException(404, "证据编号不存在")
        try:
            snapshot = load_snapshot(evidence, store.directory)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, "原文快照无法验证或为旧版记录；不能加载任意文件") from exc
        return {"evidence_id": evidence.id, "text": snapshot.text, "snapshot_hash": snapshot.snapshot_hash,
                "content_truncated": snapshot.content_truncated,
                "passages": [p.model_dump() for p in split_passages(snapshot)]}

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str, format: str = Query("html", pattern="^(html|json)$")):
        run = required(run_id)
        if format == "json":
            return JSONResponse(public_run_data(run), headers={"Content-Disposition": f'attachment; filename="{run.run_id}.json"'})
        return HTMLResponse(report_html(run), headers={"Content-Disposition": f'attachment; filename="{run.run_id}.html"'})

    dist = config.FRONTEND_DIR
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            return HTMLResponse((dist / "index.html").read_text(encoding="utf-8"))
    return app


app = create_app()
