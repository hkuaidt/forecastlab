from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
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
from .readiness import ReadinessCache
from .reporting import report_html
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


def create_app(data_dir: Path | None = None, *, question_model_factory=None) -> FastAPI:
    store = RunStore(data_dir or config.DATA_DIR)
    run_lock = Lock()
    settlement_lock = Lock()
    cancellations: dict[str, Event] = {}
    readiness = ReadinessCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.mark_interrupted()
        try:
            yield
        finally:
            for event in list(cancellations.values()):
                event.set()

    app = FastAPI(title="ForecastLab API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.state.store = store
    app.state.cancellations = cancellations
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
        dependencies = readiness.get()
        model_ready = dependencies["model"]["status"] in {"ready", "degraded"}
        search_status = dependencies["search"]["status"]
        search_ready = True if search_status == "ready" else None if search_status == "unknown" else False
        return {"ok": True, "ready": model_ready, "model_configured": bool(config.MODEL_API_KEY),
                "search_configured": bool(config.BRAVE_SEARCH_API_KEY or config.TAVILY_API_KEY),
                "model": config.MODEL_NAME, "model_ready": model_ready, "search_ready": search_ready,
                "readiness": dependencies}

    @app.get("/api/live")
    def live():
        return {"ok": True}

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

    def schedule(record, evidence, background_tasks, *, resume=False):
        event = Event()
        record.status = "queued"
        record.finished_at = None
        store.save(record)
        cancellations[record.run_id] = event

        def work():
            try:
                if event.is_set():
                    record.status = "cancelled"
                    record.finished_at = utcnow()
                    store.save(record)
                else:
                    execute(record, evidence, store, resume=resume, cancel_event=event)
            except Exception as exc:
                record.status = "cancelled" if event.is_set() else "failed"
                record.failed_stage = record.stage
                record.finished_at = utcnow()
                record.errors.append("用户已停止研究" if event.is_set() else f"执行异常：{type(exc).__name__}")
                store.save(record)
            finally:
                cancellations.pop(record.run_id, None)
                run_lock.release()

        background_tasks.add_task(work)

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
            schedule(record, evidence, background_tasks)
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
        event = cancellations.get(record.run_id)
        return {**record.model_dump(mode="json"), "report_repair": report_repair_info(record),
                "cancel_requested": bool(event and event.is_set())}

    @app.get("/api/runs")
    def list_runs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), summary: bool = False):
        return store.summaries(limit, offset) if summary else [run_view(r) for r in store.list(limit, offset)]

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

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        record = required(run_id)
        event = cancellations.get(run_id)
        if event:
            event.set()
        elif record.status in {"queued", "running"}:
            # No worker owns this record, e.g. after an interrupted process.
            record.status = "cancelled"
            record.finished_at = utcnow()
            store.save(record)
        return run_view(record)

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, background_tasks: BackgroundTasks):
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "已有预测正在运行；请等待完成后再重试。")
        try:
            record = required(run_id)
            if record.status not in {"failed", "partial", "interrupted", "cancelled"}:
                raise HTTPException(409, "只有失败、中断、已停止或部分完成的运行可以继续")
            if "forecast" in record.stage_outputs:
                raise HTTPException(409, "该运行已有完整报告")
            if not record.demo and not config.MODEL_API_KEY:
                raise HTTPException(503, "未配置模型密钥")

            schedule(record, record.evidence, background_tasks, resume=True)
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
            schedule(child, child.evidence, background_tasks, resume=True)
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
        return HTMLResponse(report_html(run, store.directory), headers={"Content-Disposition": f'attachment; filename="{run.run_id}.html"'})

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
