"""Versioned question use cases; no model request is made by confirmation."""
from __future__ import annotations
import hashlib
import json
from uuid import uuid4
from . import config
from .future_only import current_question, validate_future
from .agents.question import analyze_question, finalize_framing
from .llm import ModelClient, BudgetExceeded
from .schemas import AnalyzeQuestionRequest, QuestionFraming, ConfirmQuestionRequest, AnalysisRecord
from .storage import RunStore, VersionConflict


class ModelNotConfigured(ValueError):
    pass


class QuestionService:
    def __init__(self, store: RunStore, model_factory=None):
        self.store = store
        self.model_factory = model_factory or ModelClient
        self.requires_key = model_factory is None

    def get(self, draft_id):
        result = self.store.get_draft(draft_id)
        if not result:
            raise KeyError("问题草稿不存在")
        return result

    def confirm(self, draft_id, request: ConfirmQuestionRequest):
        framing = self.get(draft_id).framing
        if not framing.demo_case_id:
            validate_future(framing.proposed_spec)
        return self.store.confirm_draft(draft_id, request)

    def analyze(self, request: AnalyzeQuestionRequest) -> QuestionFraming:
        if request.demo_case_id:
            from .agent12_demo import validate_demo_request
            validate_demo_request(request)
        if self.requires_key and not request.demo_case_id and not config.MODEL_API_KEY:
            raise ModelNotConfigured("未配置模型密钥；真实问题不能使用固定答案代替分析。可使用教学演示。")
        raw = request.model_dump(mode="json", exclude={"operation_id"})
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        operation = self.store.get_operation(request.operation_id)
        if operation:
            if operation["input_hash"] != digest:
                raise VersionConflict("operation_id 已用于不同输入")
            if operation["status"] == "done":
                return QuestionFraming.model_validate_json(operation["data"])
            existing = self.store.get_draft(operation["draft_id"])
            if existing and existing.framing.analysis_record.input_hash == digest:
                self.store.finish_operation(request.operation_id, existing.framing)
                return existing.framing
        if not request.demo_case_id:
            request = request.model_copy(update={"question": current_question(request.question)}, deep=True)
        previous = None
        if request.draft_id:
            view = self.get(request.draft_id)
            previous = view.confirmation.framing if view.confirmation else view.framing
            if previous.demo_case_id != request.demo_case_id:
                raise ValueError("教学与真实模式不能沿用同一草稿，请创建新问题")
            if previous.revision != request.expected_revision:
                raise VersionConflict("草稿已被修改，请重新加载后分析")
        op = self.store.claim_operation(request.operation_id, digest, request.draft_id or f"draft_{uuid4().hex}")
        if op["status"] == "done":
            return QuestionFraming.model_validate_json(op["data"])
        owner = op["draft_id"]
        try:
            existing = self.store.get_draft(owner)
            if existing and existing.framing.analysis_record.input_hash == digest:
                self.store.finish_operation(request.operation_id, existing.framing)
                return existing.framing
            calls = [c for c in self.store.list_calls(owner) if c.phase == "preparation"]
            usage = {"calls": len(calls), "prompt_tokens": sum(c.prompt_tokens for c in calls),
                     "completion_tokens": sum(c.completion_tokens for c in calls)}
            model_factory = self.model_factory
            if request.demo_case_id:
                from .agent12_demo import QuestionFixtureModel
                model_factory = lambda **kwargs: QuestionFixtureModel()
            model = model_factory(initial_usage=usage, initial_active_seconds=sum(c.elapsed_seconds for c in calls),
                call_limit=6, on_reserve=lambda h, v: self.store.reserve_call(owner, "preparation", call_limit=6,
                    input_hash=h, prompt_version=v), on_finish=self.store.finish_call)
            feedback = ""
            for attempt in range(2):
                try:
                    candidate = analyze_question(request, previous, model, validation_feedback=feedback)
                    if not request.demo_case_id:
                        validate_future(candidate.proposed_spec)
                    frame = finalize_framing(candidate, request, previous)
                    frame.draft_id = owner
                    break
                except BudgetExceeded:
                    raise
                except (ValueError, RuntimeError) as exc:
                    feedback = str(exc)[:500]
                    if attempt == 1:
                        raise
            actual_calls = [c for c in self.store.list_calls(owner) if c.phase == "preparation"]
            frame.analysis_record = AnalysisRecord(model=getattr(model, "actual_model", None) or config.MODEL_NAME,
                input_hash=digest, request_ids=[c.request_id for c in actual_calls],
                elapsed_seconds=sum(c.elapsed_seconds for c in actual_calls),
                validation_mode="fixture" if request.demo_case_id else "live")
            if previous:
                self.store.append_revision(frame, expected_revision=previous.revision)
            else:
                self.store.create_draft(frame)
            self.store.finish_operation(request.operation_id, frame)
            return frame
        except Exception as exc:
            self.store.finish_operation(request.operation_id, None, type(exc).__name__)
            raise
