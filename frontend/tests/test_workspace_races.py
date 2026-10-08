"""Isolated request ordering regressions; no live research calls."""
from copy import deepcopy
import json
import pytest

from playwright.sync_api import expect
from test_agent12_browser import routes
from test_readable_workspace import detailed_run


def test_late_cancel_response_cannot_restore_running_after_terminal_poll(page, app_url):
    running = detailed_run()
    running.update(status="running", stage="simulation", forecast=None)
    routes(page, runs=[running])
    current = [running]
    pending = []
    page.route("**/api/runs/run_fixture", lambda route: route.fulfill(content_type="application/json", body=json.dumps(current[0])))
    page.route("**/api/runs/run_fixture/cancel", lambda route: pending.append(route))
    page.goto(app_url)
    page.get_by_role("button", name="停止运行", exact=True).click()
    current[0] = dict(running, status="cancelled", stage="cancelled")
    expect(page.locator(".work-status .status-label")).to_have_text("已停止")
    pending[0].fulfill(content_type="application/json", body=json.dumps(dict(running, cancel_requested=True)))
    page.wait_for_timeout(250)
    assert page.locator(".work-status .status-label").inner_text() == "已停止"
    assert "已停止" in page.locator(".directory-history").inner_text()
    expect(page.get_by_role("button", name="正在停止…", exact=True)).to_have_count(0)


@pytest.mark.parametrize("action", ["cancel", "resume", "repair-report"])
def test_stale_action_error_does_not_replace_returned_run_view(page, app_url, action):
    running = detailed_run()
    running.update(status="running", stage="simulation", forecast=None)
    if action != "cancel":
        running.update(status="failed", stage="failed", failed_stage="simulation")
        running["stage_outputs"].pop("forecast")
    if action == "repair-report":
        running["failed_stage"] = "forecast"
        running["report_repair"] = {"available": True, "reason": "合成校验失败", "endpoint": "/api/runs/run_fixture/repair-report"}
    other = deepcopy(detailed_run())
    other["run_id"] = "run_other"
    other["question"]["question"] = "另一项研究"
    routes(page, runs=[running, other])
    pending = []
    page.route("**/api/runs/run_fixture/" + action, lambda route: pending.append(route))
    page.goto(app_url + "#/research/run_fixture/report")
    page.get_by_role("button", name={"cancel": "停止运行", "resume": "从失败阶段继续 ↗", "repair-report": "检查并重试报告 ↗"}[action], exact=True).click()
    page.get_by_role("button", name="切换研究 ↗", exact=True).click()
    page.locator(".history-row").filter(has=page.get_by_text("另一项研究", exact=True)).click()
    expect(page.locator(".work-header h1")).to_have_text("另一项研究")
    page.get_by_role("button", name="切换研究 ↗", exact=True).click()
    page.locator(".history-row").filter(has=page.get_by_text(running["question"]["question"], exact=True)).click()
    expect(page.locator(".work-header h1")).to_have_text(running["question"]["question"])
    pending[0].fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "过期取消操作错误"}))
    page.wait_for_timeout(250)
    expect(page.locator(".app-alert")).to_have_count(0)


def test_legacy_summary_without_mode_is_not_mislabeled_as_historical_test(page, app_url):
    run = detailed_run()
    old_summary = {key: run[key] for key in ["run_id", "question", "status", "stage", "started_at", "finished_at", "parent_run_id", "model"]}
    old_summary["run_id"] = "run_older"
    old_summary["question"] = dict(run["question"], question="尚未打开的摘要")
    routes(page, runs=[run, old_summary])
    page.goto(app_url)
    page.get_by_role("button", name="切换研究 ↗", exact=True).click()
    row = page.locator(".history-row").filter(has=page.get_by_text("尚未打开的摘要", exact=True))
    expect(row).to_contain_text("研究记录")
    expect(row).not_to_contain_text("历史测试")


def test_cancelled_run_can_resume_saved_stages_without_completed_forecast(page, app_url):
    cancelled = detailed_run()
    cancelled.update(status="cancelled", stage="cancelled", failed_stage="simulation", forecast=None)
    cancelled["stage_outputs"].pop("forecast")
    routes(page, runs=[cancelled])
    current = [cancelled]
    page.route("**/api/runs/run_fixture", lambda route: route.fulfill(content_type="application/json", body=json.dumps(current[0])))
    def resume(route):
        assert route.request.method == "POST"
        current[0] = dict(cancelled, status="running", stage="simulation", failed_stage=None)
        route.fulfill(status=202, content_type="application/json", body=json.dumps({"run_id": "run_fixture", "status": "queued"}))
    page.route("**/api/runs/run_fixture/resume", resume)
    page.goto(app_url)
    expect(page.locator(".work-status")).to_contain_text("可继续运行或新建研究")
    page.get_by_role("button", name="从已保存阶段继续 ↗", exact=True).click()
    expect(page.locator(".work-status .status-label")).to_have_text("推演中")
    expect(page.get_by_role("button", name="从已保存阶段继续 ↗", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_be_visible()
