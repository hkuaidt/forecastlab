"""Product entry points accept future forecasts, not historical backtests."""
from datetime import timedelta
from .schemas import utcnow


def validate_future(question):
    if question.resolve_by is not None and question.resolve_by <= utcnow():
        raise ValueError("只能预测未来：预测目标时间必须晚于当前时间，请创建新的预测问题。")


def current_question(question):
    now = utcnow()
    # Allow ordinary request latency/clock skew, never a user-selected cutoff.
    if abs(question.as_of - now) > timedelta(minutes=5):
        raise ValueError("已取消历史时间选择，只能基于当前资料预测未来。")
    validate_future(question)
    return question.model_copy(update={"as_of": now}, deep=True)


def reject_exercise(evidence):
    if any(item.source_type == "exercise" for item in evidence):
        raise ValueError("已停用历史回测，不能使用历史练习证据创建或继续预测。")
