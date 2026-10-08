from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
import sqlite3
from fastapi.testclient import TestClient
from app import api, config, readiness
from app.demo import DEMO_QUESTION
from app.provenance import save_snapshot
from app.schemas import Evidence, Forecast, RunRecord, utcnow
from app.storage import RunStore


def record(name="run_test", **kwargs):
    return RunRecord(run_id=name, question=DEMO_QUESTION.model_copy(deep=True), evidence_mode="demo",
                     model="fixture", demo=True, **kwargs)


def test_paginated_summaries_are_small_and_reach_older_records(tmp_path):
    app = api.create_app(tmp_path)
    now = utcnow()
    for i in range(55):
        run = record(f"run_{i:03}", status="completed", started_at=now-timedelta(seconds=i), stage_outputs={"large": "x"*30000})
        app.state.store.save(run, snapshot=False)
    with TestClient(app) as client:
        first = client.get("/api/runs?summary=true&limit=20").json()
        last = client.get("/api/runs?summary=true&limit=20&offset=40").json()
        assert len(first) == 20 and len(last) == 15
        assert last[-1]["run_id"] == "run_054"
        assert "stage_outputs" not in first[0] and "question" in first[0]
        assert first[0]["evidence_mode"] == "demo"
        assert len(client.get("/api/runs?summary=true&limit=20").content) < 30000
        assert client.get("/api/runs/run_054").json()["stage_outputs"]["large"] == "x"*30000
        assert client.get("/api/runs?offset=-1").status_code == 422
        assert client.get("/api/runs?limit=100000").status_code == 422


def test_summary_migration_and_updates(tmp_path):
    old = record(status="completed")
    con = sqlite3.connect(tmp_path / "forecastlab.sqlite3")
    con.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY,status TEXT,started_at TEXT,data TEXT)")
    con.execute("INSERT INTO runs VALUES(?,?,?,?)", (old.run_id, old.status, old.started_at.isoformat(), old.model_dump_json()))
    con.commit(); con.close()
    store = RunStore(tmp_path)
    assert store.summaries()[0]["status"] == "completed"
    old.status = "failed"
    old.errors = ["error"]
    store.save(old)
    assert store.summaries()[0]["last_error"] == "error"


def test_partial_report_is_not_mislabeled_as_rejected():
    run = record(status="partial", forecast=Forecast(status="partial", conclusion="保留证据摘要"))
    assert not RunStore.summary(run)["report_failed"]
    assert "状态：部分完成" in api.report_html(run)
    run.failed_stage = "forecast"
    assert RunStore.summary(run)["report_failed"]
    assert "状态：报告待修复" in api.report_html(run)


def test_cancel_stops_worker_and_releases_slot(tmp_path, monkeypatch):
    entered = Event()
    def fake_execute(run, evidence, store, *, resume=False, cancel_event=None):
        run.status = "running"; store.save(run)
        entered.set()
        assert cancel_event.wait(5), "cancel did not reach worker"
        run.status = "cancelled"; run.finished_at = utcnow(); store.save(run)
    monkeypatch.setattr(api, "execute", fake_execute)
    app = api.create_app(tmp_path)
    with TestClient(app) as client:
        results = []
        thread = Thread(target=lambda: results.append(client.post("/api/runs", json={"question": DEMO_QUESTION.model_dump(mode="json"), "evidence_mode": "demo"})))
        thread.start()
        assert entered.wait(3)
        run_id = app.state.store.list()[0].run_id
        result = client.post(f"/api/runs/{run_id}/cancel")
        assert result.status_code == 200
        thread.join(3)
        assert not thread.is_alive()
        assert results[0].status_code == 202
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "cancelled"
        assert not app.state.cancellations
        # A later task can reserve the same application slot immediately.
        def instant(run, evidence, store, **kwargs):
            run.status = "completed"; store.save(run)
        monkeypatch.setattr(api, "execute", instant)
        assert client.post("/api/runs", json={"question": DEMO_QUESTION.model_dump(mode="json"), "evidence_mode": "demo"}).status_code == 202


def test_health_separates_liveness_configuration_and_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-key")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(readiness.ReadinessCache, "get", lambda self: {
        "model": {"status": "unavailable", "detail": "offline"},
        "search": {"status": "unknown", "transport_ready": True, "detail": "quota unknown"}})
    with TestClient(api.create_app(tmp_path)) as client:
        data = client.get("/api/health").json()
        assert data["ok"] and data["model_configured"]
        assert not data["ready"] and not data["model_ready"]
        assert data["search_ready"] is None
        assert client.get("/api/live").json() == {"ok": True}


def test_readiness_is_cached_without_generating_or_searching(monkeypatch):
    calls = []
    monkeypatch.setattr(readiness, "model_readiness", lambda: calls.append("model") or {"status": "ready"})
    monkeypatch.setattr(readiness, "search_readiness", lambda: calls.append("search") or {"status": "unknown"})
    cache = readiness.ReadinessCache()
    cache.get(); cache.get()
    assert sorted(calls) == ["model", "search"]


def test_export_preserves_long_source_and_marks_excerpt(tmp_path):
    full = "source "*2000 + "FINAL_SOURCE_PARAGRAPH"
    snap = save_snapshot(full, {}, tmp_path)
    evidence = Evidence(id="E001", source_url="https://example.org", title="source", excerpt=full[:12000],
        claim="source", retrieved_at=utcnow(), source_type="imported", content_hash="unused", source_group="example.org",
        snapshot_path=snap.snapshot_path, snapshot_hash=snap.snapshot_hash)
    run = record(status="completed", parent_run_id="run_parent", evidence=[evidence])
    run.question.mode = "scenario"
    run.forecast = Forecast(status="completed", conclusion="<script>test</script>", probabilities={"基准": .6, "其他": .4})
    full_html = api.report_html(run, tmp_path)
    assert "FINAL_SOURCE_PARAGRAPH" in full_html and "完整已保存原文快照" in full_html
    assert "父研究：run_parent" in full_html
    assert "结算规则：" not in full_html and "实际结果与评分" not in full_html
    assert "&lt;script&gt;" in full_html and "<script>" not in full_html
    excerpt_html = api.report_html(run)
    assert "不是完整原文" in excerpt_html and "FINAL_SOURCE_PARAGRAPH" not in excerpt_html
    run.evidence[0].content_kind = "snippet"
    run.evidence[0].source_type = "snippet_only"
    snippet_html = api.report_html(run, tmp_path)
    assert "搜索摘要快照（未取得网页正文）" in snippet_html
    assert "完整已保存原文快照" not in snippet_html


def test_failed_export_retains_warning_and_all_reference_types():
    from app.schemas import Claim, Review, ReviewIssue
    run = record(status="partial", errors=["报告生成失败"])
    run.forecast = Forecast(status="partial", conclusion="需要补充", opposing=[Claim(text="依据", evidence_ids=["E001"], assumption_ids=["H001"], simulation_ids=["S1"])])
    run.review = Review(status="blocked", issues=[ReviewIssue(severity="high", claim="待查", explanation="原文尚待核查")])
    html = api.report_html(run)
    assert "模型提出的待核查问题" in html and "报告生成失败" in html
    assert "证据：E001" in html and "假设：H001" in html and "模拟：S1" in html


def test_html_export_preserves_confirmed_scope_answers():
    from app.schemas import QuestionFraming, QuestionDraft, QuestionClarification
    run = record()
    run.question_framing = QuestionFraming(draft_id="draft_export", revision=1,
        raw_question=run.question.question,
        proposed_spec=QuestionDraft(question=run.question.question, as_of=run.question.as_of, mode="scenario"),
        status="ready_for_confirmation",
        clarifications=[QuestionClarification(id="C001", field="研究对象", question="研究范围是什么？",
            answer="仅限 Lean 证明检查与论文评审。", status="resolved")],
        alternative_directions=["比较验证成本与评审效率"])
    html = api.report_html(run)
    assert "已确认的澄清与研究范围" in html
    assert "仅限 Lean 证明检查与论文评审。" in html
    assert "比较验证成本与评审效率" in html
