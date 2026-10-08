"""New research is scenario-only, with an explicit current-time submission policy."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

import pytest
from playwright.sync_api import expect


QUESTION = "既然测试已完成，项目未来三个月的发布进展会如何演变？"
INITIAL = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 8, 8, 30, tzinfo=timezone.utc)
LATEST = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def scenario_routes(page, *, fail_first=False, historical_binary=False):
    state = {"analyses": [], "confirmations": [], "runs": [], "frame": None}
    old_question = {"id": "Q1", "question": "旧版项目能否按期发布正式版？", "mode": "binary",
        "as_of": "2026-09-30T08:00:00Z", "resolve_by": "2026-11-30T08:00:00Z",
        "resolution_rule": "以正式版上线为是", "resolution_source": "https://example.org/releases",
        "user_assumptions": ["旧条件"], "outcomes": ["是", "否"]}
    old_run = {"run_id": "old_binary", "question": old_question, "question_origin": "legacy_direct",
        "question_framing": None, "confirmation_id": None, "parent_run_id": None,
        "question_version": 1, "evidence_mode": "import", "demo": False,
        "status": "completed", "stage": "done", "stage_outputs": {},
        "question_analysis": None, "evidence": [], "evidence_assessment": None, "world": None,
        "actions": [], "simulation": [], "review": None, "forecast": None, "settlement": None,
        "model": "fixture", "started_at": old_question["as_of"], "finished_at": old_question["as_of"],
        "usage": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}, "errors": []}
    state["historical"] = deepcopy(old_run)

    def handle(route):
        path = route.request.url.split("/api", 1)[1].split("?", 1)[0]
        status = 200
        if path == "/health":
            data = {"ok": True, "model_configured": True, "search_configured": True, "model": "fixture"}
        elif path == "/examples":
            data = {"presets": []}
        elif path == "/settlements/summary":
            data = {"settled_count": 0, "scored_count": 0, "average_brier": None}
        elif path == "/questions/analyze":
            request = route.request.post_data_json
            state["analyses"].append(request)
            if fail_first and len(state["analyses"]) == 1:
                data = {"detail": "暂时无法连接，请重试。"}
                status = 503
            else:
                question = deepcopy(request["question"])
                state["frame"] = {"schema_version": 1, "draft_id": "scenario_draft",
                    "revision": request.get("expected_revision", 0) + 1,
                    "raw_question": question["question"], "proposed_spec": question,
                    "inputs": [{"input_id": "I001", "kind": "original", "text": question["question"]}],
                    "clarifications": [], "premises": [{"id": "P001", "content": "测试已完成",
                        "origin": "user_explicit", "source_input_id": "I001", "original_span": "测试已完成",
                        "rationale": "核查用户明确提出的事实", "user_review": "pending",
                        "treatment": "to_verify", "replaces_id": None}],
                    "alternative_directions": [], "retrieval_plan": [],
                    "status": "ready_for_confirmation", "analysis_record": {"validation_mode": "fixture"},
                    "demo_case_id": None}
                data = state["frame"]
        elif path.endswith("/confirm"):
            state["confirmations"].append(route.request.post_data_json)
            data = {"confirmation_id": "scenario_confirmation", "framing": state["frame"],
                    "question": state["frame"]["proposed_spec"], "revision": state["frame"]["revision"]}
        elif path == "/questions/scenario_draft":
            data = {"framing": state["frame"], "confirmation": None}
        elif path == "/runs" and route.request.method == "POST":
            state["runs"].append(route.request.post_data_json)
            data = {"run_id": "old_binary", "status": "queued"}
            status = 202
        elif path == "/runs":
            data = [old_run] if historical_binary else []
        elif path == "/runs/old_binary":
            data = old_run
        else:
            data = {"detail": "unknown test route"}
            status = 404
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    page.route("**/api/**", handle)
    return state


def open_composer(page, app_url):
    page.goto(app_url)
    page.get_by_role("button", name="新建研究 ＋", exact=True).click()
    return page.locator(".research-dialog")


def assert_scenario_request(request):
    question = request["question"]
    assert question["question"] == QUESTION
    assert question["mode"] == "scenario"
    assert question["resolve_by"] is None
    assert question["resolution_rule"] == ""
    assert question["resolution_source"] is None
    assert question["user_assumptions"] == []


@pytest.mark.parametrize("width", [1440, 390])
def test_new_research_shows_question_and_current_time_without_binary_fields(page, app_url, width):
    scenario_routes(page)
    page.set_viewport_size({"width": width, "height": 900})
    dialog = open_composer(page, app_url)
    expect(dialog.get_by_label("研究问题", exact=True)).to_be_visible()
    expect(dialog.get_by_text("默认使用提交分析时的当前时间。", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="指定历史时间", exact=True)).to_be_visible()
    for label in ("分析方式", "结果截止时间", "如何判断结果", "结果来源", "用户指定条件（每行一条）"):
        expect(dialog.get_by_label(label, exact=True)).to_have_count(0)
    expect(dialog.get_by_text("结算规则与情景条件", exact=True)).to_have_count(0)
    expect(dialog.locator("select")).to_have_count(0)
    expect(dialog.locator('input[type="datetime-local"]')).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_default_cutoff_is_analysis_time_and_explicit_question_premises_survive(page, app_url):
    state = scenario_routes(page)
    page.clock.install(time=INITIAL)
    dialog = open_composer(page, app_url)
    dialog.get_by_label("研究问题", exact=True).fill(QUESTION)
    page.clock.set_fixed_time(LATER)
    dialog.get_by_role("button", name="分析问题", exact=True).click()
    expect(dialog.get_by_label("P001 前提处理")).to_be_visible()
    assert_scenario_request(state["analyses"][0])
    assert state["analyses"][0]["question"]["as_of"] == "2026-10-08T08:30:00.000Z"
    expect(dialog.get_by_text("核查用户明确提出的事实", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="确认问题", exact=True)).to_be_disabled()
    dialog.get_by_label("P001 前提处理").select_option("to_verify")
    dialog.get_by_role("button", name="确认问题", exact=True).click()
    expect(dialog.get_by_role("button", name=re.compile("^开始联网推演"))).to_be_enabled()
    assert state["confirmations"][0]["decisions"] == [
        {"premise_id": "P001", "user_review": "retained", "treatment": "to_verify"}]


def test_explicit_history_time_is_preserved_and_switching_to_current_invalidates_confirmation(page, app_url):
    state = scenario_routes(page)
    page.clock.install(time=INITIAL)
    dialog = open_composer(page, app_url)
    dialog.get_by_label("研究问题", exact=True).fill(QUESTION)
    dialog.get_by_role("button", name="指定历史时间", exact=True).click()
    dialog.get_by_label("信息截至", exact=True).fill("2026-09-30T16:00")
    expected = page.evaluate("new Date('2026-09-30T16:00').toISOString()")
    page.clock.set_fixed_time(LATER)
    dialog.get_by_role("button", name="分析问题", exact=True).click()
    expect(dialog.get_by_label("P001 前提处理")).to_be_visible()
    assert state["analyses"][0]["question"]["as_of"] == expected
    dialog.get_by_label("P001 前提处理").select_option("to_verify")
    dialog.get_by_role("button", name="确认问题", exact=True).click()
    expect(dialog.get_by_role("button", name=re.compile("^开始联网推演"))).to_be_enabled()
    dialog.get_by_role("button", name="使用当前时间", exact=True).click()
    expect(dialog.get_by_role("button", name=re.compile("^开始联网推演"))).to_be_disabled()
    page.clock.set_fixed_time(LATEST)
    dialog.get_by_role("button", name="重新分析修改后的问题", exact=True).click()
    expect(dialog.get_by_text("内容已修改，请重新分析后确认。", exact=True)).to_have_count(0)
    assert_scenario_request(state["analyses"][1])
    assert state["analyses"][1]["question"]["as_of"] == "2026-10-08T09:00:00.000Z"


def test_failed_current_time_request_reuses_operation_and_cutoff_but_new_revision_refreshes_them(page, app_url):
    state = scenario_routes(page, fail_first=True)
    page.clock.install(time=INITIAL)
    dialog = open_composer(page, app_url)
    dialog.get_by_label("研究问题", exact=True).fill(QUESTION)
    dialog.get_by_role("button", name="分析问题", exact=True).click()
    expect(dialog.get_by_role("alert")).to_have_text("暂时无法连接，请重试。")
    page.clock.set_fixed_time(LATER)
    dialog.get_by_role("button", name="分析问题", exact=True).click()
    expect(dialog.get_by_label("P001 前提处理")).to_be_visible()
    assert state["analyses"][1] == state["analyses"][0]
    page.clock.set_fixed_time(LATEST)
    dialog.get_by_role("button", name="分析问题", exact=True).click()
    expect(dialog.get_by_role("button", name="分析问题", exact=True)).to_be_enabled()
    assert len(state["analyses"]) == 3
    assert state["analyses"][2]["operation_id"] != state["analyses"][1]["operation_id"]
    assert state["analyses"][2]["question"]["as_of"] == "2026-10-08T09:00:00.000Z"


def test_saved_binary_run_does_not_seed_new_scenario_fields(page, app_url):
    state = scenario_routes(page, historical_binary=True)
    dialog = open_composer(page, app_url)
    expect(dialog.get_by_label("研究问题", exact=True)).to_have_value("")
    dialog.get_by_label("研究问题", exact=True).fill(QUESTION)
    dialog.get_by_role("button", name="分析问题", exact=True).click()
    expect(dialog.get_by_label("P001 前提处理")).to_be_visible()
    assert_scenario_request(state["analyses"][0])
    saved = page.evaluate("fetch('/api/runs/old_binary').then(response => response.json())")
    assert saved["question"]["mode"] == "binary"
    assert saved["question"]["resolution_rule"] == "以正式版上线为是"
    assert saved["question"] == state["historical"]["question"]
