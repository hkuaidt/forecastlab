"""SQLite index plus immutable, per-stage JSON snapshots for replay."""
from __future__ import annotations
import json
import hashlib
from uuid import uuid4
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from .schemas import (RunRecord, utcnow, QuestionFraming, QuestionSpec, QuestionConfirmation,
                      ConfirmQuestionRequest, DraftView, ModelCallRecord)


class VersionConflict(ValueError):
    """A stale write/confirmation must not replace another tab's work."""


def decision_digest(request: ConfirmQuestionRequest) -> str:
    rows = sorted([d.model_dump() for d in request.decisions], key=lambda d: d["premise_id"])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RunStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.snapshots = self.directory / "runs"
        self.snapshots.mkdir(exist_ok=True)
        self.db = self.directory / "forecastlab.sqlite3"
        self.lock = RLock()
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL, data TEXT NOT NULL)")
            con.execute("CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at DESC)")
            if "summary" not in {row[1] for row in con.execute("PRAGMA table_info(runs)")}:
                con.execute("ALTER TABLE runs ADD COLUMN summary TEXT")
            for row in con.execute("SELECT run_id,data FROM runs WHERE summary IS NULL").fetchall():
                record = RunRecord.model_validate_json(row["data"])
                con.execute("UPDATE runs SET summary=? WHERE run_id=?", (json.dumps(self.summary(record), ensure_ascii=False), row["run_id"]))
            con.executescript("""
                CREATE TABLE IF NOT EXISTS question_drafts (draft_id TEXT PRIMARY KEY, latest_revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS question_revisions (draft_id TEXT, revision INTEGER, data TEXT NOT NULL,
                    PRIMARY KEY(draft_id, revision));
                CREATE TABLE IF NOT EXISTS question_confirmations (confirmation_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL, revision INTEGER NOT NULL, decision_hash TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(draft_id, revision));
                CREATE TABLE IF NOT EXISTS model_calls (request_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    phase TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS calls_owner ON model_calls(owner_id);
                CREATE TABLE IF NOT EXISTS analysis_operations (operation_id TEXT PRIMARY KEY,
                    input_hash TEXT NOT NULL, draft_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT, error TEXT);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def save(self, record: RunRecord, snapshot: bool = True):
        data = record.model_dump_json()
        summary = json.dumps(self.summary(record), ensure_ascii=False)
        with self.lock, self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            exists = con.execute("SELECT 1 FROM runs WHERE run_id=?", (record.run_id,)).fetchone()
            if not exists and record.confirmation_id:
                row = con.execute("SELECT c.revision,d.latest_revision FROM question_confirmations c JOIN question_drafts d "
                                  "ON c.draft_id=d.draft_id WHERE c.confirmation_id=?", (record.confirmation_id,)).fetchone()
                if not row or row["revision"] != row["latest_revision"]:
                    raise VersionConflict("确认已失效，创建运行前请重新确认")
            con.execute("INSERT INTO runs(run_id,status,started_at,data,summary) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,data=excluded.data,summary=excluded.summary", (record.run_id, record.status, record.started_at.isoformat(), data, summary))
        if snapshot:
            folder = self.snapshots / record.run_id
            folder.mkdir(exist_ok=True)
            stage = record.stage.replace("/", "_")
            temp = folder / f".{stage}.tmp"
            temp.write_text(data, encoding="utf-8")
            temp.replace(folder / f"{stage}.json")

    def get(self, run_id: str) -> RunRecord | None:
        with self.lock, self.connect() as con:
            row = con.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return RunRecord.model_validate_json(row["data"]) if row else None

    @staticmethod
    def summary(record: RunRecord) -> dict:
        from .presentation import report_failed
        return {"run_id": record.run_id, "question": record.question.model_dump(mode="json"),
                "status": record.status, "stage": record.stage, "started_at": record.started_at.isoformat(),
                "finished_at": record.finished_at.isoformat() if record.finished_at else None,
                "parent_run_id": record.parent_run_id, "model": record.model,
                "demo": record.demo, "is_demo": record.demo, "evidence_mode": record.evidence_mode,
                "last_error": record.errors[-1] if record.errors else None,
                "report_failed": report_failed(record)}

    def summaries(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.lock, self.connect() as con:
            rows = con.execute("SELECT summary FROM runs ORDER BY started_at DESC,run_id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [json.loads(row["summary"]) for row in rows]

    def list(self, limit: int = 50, offset: int = 0) -> list[RunRecord]:
        with self.lock, self.connect() as con:
            rows = con.execute("SELECT data FROM runs ORDER BY started_at DESC,run_id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [RunRecord.model_validate_json(row["data"]) for row in rows]

    def create_draft(self, framing: QuestionFraming) -> QuestionFraming:
        if framing.revision != 1:
            raise ValueError("首次草稿版本必须为1")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM question_drafts WHERE draft_id=?", (framing.draft_id,)).fetchone():
                raise VersionConflict("草稿已存在，请提交新版本")
            con.execute("INSERT INTO question_drafts VALUES(?,?)", (framing.draft_id, 1))
            con.execute("INSERT INTO question_revisions VALUES(?,?,?)", (framing.draft_id, 1, framing.model_dump_json()))
        return framing

    def append_revision(self, framing: QuestionFraming, *, expected_revision: int) -> QuestionFraming:
        if framing.revision != expected_revision + 1:
            raise VersionConflict("新版本必须紧接预期版本")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            updated = con.execute("UPDATE question_drafts SET latest_revision=? WHERE draft_id=? AND latest_revision=?",
                                  (framing.revision, framing.draft_id, expected_revision))
            if updated.rowcount != 1:
                raise VersionConflict("草稿版本已变化，请重新加载")
            con.execute("INSERT INTO question_revisions VALUES(?,?,?)",
                        (framing.draft_id, framing.revision, framing.model_dump_json()))
        return framing

    def get_revision(self, draft_id: str, revision: int) -> QuestionFraming | None:
        with self.connect() as con:
            row = con.execute("SELECT data FROM question_revisions WHERE draft_id=? AND revision=?", (draft_id, revision)).fetchone()
        return QuestionFraming.model_validate_json(row["data"]) if row else None

    def get_draft(self, draft_id: str) -> DraftView | None:
        with self.connect() as con:
            # A read transaction gives a consistent revision/confirmation pair.
            con.execute("BEGIN")
            row = con.execute("SELECT r.data,r.revision FROM question_revisions r JOIN question_drafts d "
                              "ON r.draft_id=d.draft_id AND r.revision=d.latest_revision WHERE d.draft_id=?", (draft_id,)).fetchone()
            if not row:
                return None
            confirmation = con.execute("SELECT data FROM question_confirmations WHERE draft_id=? AND revision=?",
                                       (draft_id, row["revision"])).fetchone()
            calls = con.execute("SELECT data FROM model_calls WHERE owner_id=? AND phase='preparation' ORDER BY rowid", (draft_id,)).fetchall()
        return DraftView(framing=QuestionFraming.model_validate_json(row["data"]),
                         confirmation=QuestionConfirmation.model_validate_json(confirmation["data"]) if confirmation else None,
                         preparation_records=[ModelCallRecord.model_validate_json(c["data"]) for c in calls])

    def confirm_draft(self, draft_id: str, request: ConfirmQuestionRequest) -> QuestionConfirmation:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            latest = con.execute("SELECT latest_revision FROM question_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if not latest:
                raise KeyError("问题草稿不存在")
            if latest["latest_revision"] != request.expected_revision:
                raise VersionConflict("草稿版本已变化，请重新确认")
            frame = QuestionFraming.model_validate_json(con.execute(
                "SELECT data FROM question_revisions WHERE draft_id=? AND revision=?", (draft_id, request.expected_revision)).fetchone()["data"])
            digest = decision_digest(request)
            saved = con.execute("SELECT data,decision_hash FROM question_confirmations WHERE draft_id=? AND revision=?",
                                (draft_id, request.expected_revision)).fetchone()
            if saved:
                if saved["decision_hash"] != digest:
                    raise VersionConflict("该版本已确认；修改决定需要新草稿版本")
                return QuestionConfirmation.model_validate_json(saved["data"])
            if frame.status != "ready_for_confirmation" or any(c.blocking and c.status == "open" for c in frame.clarifications):
                raise ValueError("请先回答阻断性澄清问题并重新分析")
            ids = [d.premise_id for d in request.decisions]
            if len(ids) != len(set(ids)) or set(ids) != {p.id for p in frame.premises}:
                raise ValueError("必须对本版本每项前提作出一次保留或否认决定")
            decisions = {d.premise_id: d for d in request.decisions}
            for premise in frame.premises:
                choice = decisions[premise.id]
                premise.user_review = choice.user_review
                premise.treatment = choice.treatment if choice.user_review == "retained" else "to_verify"
            active = {p.id for p in frame.premises if p.user_review == "retained"}
            tasks = []
            for task in frame.retrieval_plan:
                old_targets = task.target_premise_ids
                task.target_premise_ids = [pid for pid in old_targets if pid in active]
                if not old_targets or set(old_targets) <= active:
                    tasks.append(task)
            frame.retrieval_plan = tasks
            spec = QuestionSpec.model_validate(frame.proposed_spec.model_dump())
            confirmation = QuestionConfirmation(confirmation_id=f"confirm_{uuid4().hex}", draft_id=draft_id,
                revision=frame.revision, framing=frame, question=spec, decision_hash=digest, demo_case_id=frame.demo_case_id)
            con.execute("INSERT INTO question_confirmations VALUES(?,?,?,?,?)", (confirmation.confirmation_id,
                draft_id, frame.revision, digest, confirmation.model_dump_json()))
        return confirmation

    def get_confirmation(self, confirmation_id: str, *, require_current: bool = True) -> QuestionConfirmation | None:
        with self.connect() as con:
            row = con.execute("SELECT c.data,c.revision,d.latest_revision FROM question_confirmations c "
                              "JOIN question_drafts d ON c.draft_id=d.draft_id WHERE c.confirmation_id=?", (confirmation_id,)).fetchone()
        if not row:
            return None
        if require_current and row["revision"] != row["latest_revision"]:
            raise VersionConflict("确认对应旧草稿，请重新分析并确认")
        return QuestionConfirmation.model_validate_json(row["data"])

    def get_operation(self, operation_id: str) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM analysis_operations WHERE operation_id=?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def claim_operation(self, operation_id: str, input_hash: str, draft_id: str) -> dict:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM analysis_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row:
                if row["input_hash"] != input_hash:
                    raise VersionConflict("operation_id 已用于不同输入，请重新提交")
                if row["status"] == "done":
                    return {"status": "done", "draft_id": row["draft_id"], "data": row["data"]}
                if row["status"] == "running":
                    raise VersionConflict("该分析正在执行，请勿重复提交")
                con.execute("UPDATE analysis_operations SET status='running',error=NULL WHERE operation_id=?", (operation_id,))
                return {"status": "retry", "draft_id": row["draft_id"]}
            con.execute("INSERT INTO analysis_operations VALUES(?,?,?,'running',NULL,NULL)", (operation_id, input_hash, draft_id))
        return {"status": "new", "draft_id": draft_id}

    def finish_operation(self, operation_id: str, framing: QuestionFraming | None, error: str | None = None):
        with self.connect() as con:
            con.execute("UPDATE analysis_operations SET status=?,data=?,error=? WHERE operation_id=?",
                        ("done" if framing else "failed", framing.model_dump_json() if framing else None, error, operation_id))

    def reserve_call(self, owner_id: str, phase: str, *, call_limit: int, input_hash: str, prompt_version: str) -> ModelCallRecord:
        from .llm import BudgetExceeded
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute("SELECT COUNT(*) FROM model_calls WHERE owner_id=? AND phase=?", (owner_id, phase)).fetchone()[0]
            if count >= call_limit:
                raise BudgetExceeded("模型请求额度已用完，已保留已有结果")
            record = ModelCallRecord(request_id=f"request_{uuid4().hex}", owner_id=owner_id, phase=phase,
                                     attempt=count+1, input_hash=input_hash, prompt_version=prompt_version)
            con.execute("INSERT INTO model_calls VALUES(?,?,?,?,?)", (record.request_id, owner_id, phase, record.status, record.model_dump_json()))
        return record

    def finish_call(self, record: ModelCallRecord) -> None:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM model_calls WHERE request_id=?", (record.request_id,)).fetchone()
            if not row:
                raise KeyError("请求记录不存在")
            saved = ModelCallRecord.model_validate_json(row["data"])
            if (saved.owner_id, saved.phase, saved.input_hash) != (record.owner_id, record.phase, record.input_hash):
                raise ValueError("不能改变请求所属者或输入")
            if saved.status not in {"reserved", "interrupted"} and saved != record:
                raise VersionConflict("已结束请求不可覆盖")
            con.execute("UPDATE model_calls SET status=?,data=? WHERE request_id=?",
                        (record.status, record.model_dump_json(), record.request_id))

    def list_calls(self, owner_id: str) -> list[ModelCallRecord]:
        with self.connect() as con:
            rows = con.execute("SELECT data FROM model_calls WHERE owner_id=? ORDER BY rowid", (owner_id,)).fetchall()
        return [ModelCallRecord.model_validate_json(row["data"]) for row in rows]

    def mark_interrupted(self):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for row in con.execute("SELECT data FROM model_calls WHERE status='reserved'").fetchall():
                call = ModelCallRecord.model_validate_json(row["data"])
                call.status = "interrupted"
                call.elapsed_seconds = max(0, min(300, (utcnow() - call.started_at).total_seconds()))
                call.error_type = "ProcessInterrupted"
                con.execute("UPDATE model_calls SET status=?,data=? WHERE request_id=?", (call.status, call.model_dump_json(), call.request_id))
            con.execute("UPDATE analysis_operations SET status='failed',error='ProcessInterrupted' WHERE status='running'")
        for run in self.list(1000):
            if run.status in ("queued", "running"):
                run.status = "interrupted"
                run.stage = "interrupted"
                run.errors.append("服务重启时任务未完成；可从历史记录重新运行。")
                run.finished_at = utcnow()
                self.save(run)
