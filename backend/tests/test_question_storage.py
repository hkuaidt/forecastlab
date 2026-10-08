from concurrent.futures import ThreadPoolExecutor
import pytest
from app.storage import RunStore
from app.llm import BudgetExceeded
from app.schemas import QuestionFraming, ConfirmQuestionRequest, PremiseDecision


def init_store(tmp_path, clear_framing):
    store = RunStore(tmp_path)
    assert hasattr(store, "create_draft"), "missing draft storage"
    frame = QuestionFraming.model_validate(clear_framing)
    store.create_draft(frame)
    return store, frame


def decision(review="retained", treatment="to_verify"):
    return ConfirmQuestionRequest(expected_revision=1, decisions=[
        PremiseDecision(premise_id="P001", user_review=review, treatment=treatment)])


def test_revision_compare_and_swap(tmp_path, clear_framing):
    store, frame = init_store(tmp_path, clear_framing)
    other = RunStore(tmp_path)
    revision2 = frame.model_copy(deep=True, update={"revision": 2})
    store.append_revision(revision2, expected_revision=1)
    from app.storage import VersionConflict
    with pytest.raises(VersionConflict):
        other.append_revision(revision2, expected_revision=1)
    assert store.get_draft(frame.draft_id).framing.revision == 2
    assert store.get_revision(frame.draft_id, 1).raw_question == frame.raw_question


def test_same_confirmation_is_idempotent(tmp_path, clear_framing):
    store, frame = init_store(tmp_path, clear_framing)
    a = store.confirm_draft(frame.draft_id, decision())
    b = RunStore(tmp_path).confirm_draft(frame.draft_id, decision())
    assert a.confirmation_id == b.confirmation_id
    assert a.framing.premises[0].user_review == "retained"
    assert a.question.user_assumptions == []


def test_confirmation_cannot_be_overwritten(tmp_path, clear_framing):
    store, frame = init_store(tmp_path, clear_framing)
    confirmed = store.confirm_draft(frame.draft_id, decision())
    from app.storage import VersionConflict
    with pytest.raises(VersionConflict):
        store.confirm_draft(frame.draft_id, decision("rejected"))
    store.append_revision(frame.model_copy(deep=True, update={"revision": 2}), expected_revision=1)
    with pytest.raises(VersionConflict):
        store.get_confirmation(confirmed.confirmation_id)
    assert store.get_confirmation(confirmed.confirmation_id, require_current=False).revision == 1


def test_all_premises_need_decisions_and_blockers_cannot_be_ignored(tmp_path, clear_framing):
    store, frame = init_store(tmp_path, clear_framing)
    with pytest.raises(ValueError):
        store.confirm_draft(frame.draft_id, ConfirmQuestionRequest(expected_revision=1))
    duplicate = decision(); duplicate.decisions *= 2
    with pytest.raises(ValueError):
        store.confirm_draft(frame.draft_id, duplicate)


def test_rejection_removes_active_targets(tmp_path, clear_framing):
    store, frame = init_store(tmp_path, clear_framing)
    confirmed = store.confirm_draft(frame.draft_id, decision("rejected", "scenario_condition"))
    assert confirmed.framing.premises[0].user_review == "rejected"
    assert confirmed.question.user_assumptions == []
    assert all("P001" not in t.target_premise_ids for t in confirmed.framing.retrieval_plan)
    assert not any(t.query == "青岚测试进展" for t in confirmed.framing.retrieval_plan)


def test_call_reservation_is_atomic(tmp_path):
    store = RunStore(tmp_path)
    assert hasattr(store, "reserve_call")
    def reserve(_):
        try:
            return RunStore(tmp_path).reserve_call("draft_budget", "preparation", call_limit=6,
                input_hash="fixed", prompt_version="question-evidence-v1")
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert len([r for r in results if r]) == 6
    assert len(store.list_calls("draft_budget")) == 6
    with pytest.raises(BudgetExceeded):
        reserve_store = RunStore(tmp_path)
        reserve_store.reserve_call("draft_budget", "preparation", call_limit=6, input_hash="fixed", prompt_version="question-evidence-v1")


def test_reopen_keeps_history_and_interrupted_reservations(tmp_path, clear_framing):
    store, frame = init_store(tmp_path, clear_framing)
    confirmed = store.confirm_draft(frame.draft_id, decision())
    call = store.reserve_call(frame.draft_id, "preparation", call_limit=6, input_hash="x", prompt_version="v1")
    other = RunStore(tmp_path)
    other.mark_interrupted()
    assert other.get_draft(frame.draft_id).confirmation.confirmation_id == confirmed.confirmation_id
    saved = other.list_calls(frame.draft_id)
    assert saved[0].request_id == call.request_id and saved[0].status == "interrupted"
    assert saved[0].usage_known is False


def test_analysis_operation_is_idempotent_and_checks_payload(tmp_path):
    store = RunStore(tmp_path)
    assert hasattr(store, "claim_operation")
    assert store.claim_operation("op1", "hash1", "draft_a")["status"] == "new"
    from app.storage import VersionConflict
    with pytest.raises(VersionConflict):
        RunStore(tmp_path).claim_operation("op1", "hash2", "draft_b")
    with pytest.raises(VersionConflict):
        RunStore(tmp_path).claim_operation("op1", "hash1", "draft_b")
