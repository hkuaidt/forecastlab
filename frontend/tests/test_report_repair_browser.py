"""A failed final report stays visible as a failure and repairs in a new run."""
from copy import deepcopy
import json
import re

import pytest
from playwright.sync_api import expect

from test_agent12_browser import RUN, routes


def rejected_run():
    run = deepcopy(RUN)
    run.update({
        "status": "scenario_only", "stage": "done", "failed_stage": None,
        "started_at": "2026-09-01T08:00:00Z", "finished_at": "2026-10-08T08:00:00Z",
        "active_seconds": 125.9,
        "stage_durations": {"question": 20, "evidence": 30, "world": 15, "simulation": 40, "review": 10, "forecast": 10},
        "stage_outputs": {phase: {"saved": True} for phase in ("question", "evidence", "world", "simulation", "review", "forecast")},
        "usage": {"calls": 6, "prompt_tokens": 2500, "completion_tokens": 1250},
        "report_repair": {"available": True, "reason": "需要重新核验来源后修复报告",
                          "endpoint": "/api/runs/run_fixture/repair-report"},
        "forecast": {
            "status": "scenario_only", "conclusion": "报告生成未通过结构校验，当前不能形成可靠结论。", "probabilities": None,
            "supporting": [{"text": "被拒绝的支持主张不可作为研究结果", "evidence_ids": ["F999"], "assumption_ids": [], "simulation_ids": []}],
            "opposing": [], "scenarios": ["被拒绝的情景结果不可展示"], "new_information": ["被拒绝的新增断言不可展示"],
            "limitations": ["自动报告的完整性校验未通过"],
        },
    })
    run["forecast_attempts"] = [{
        "candidate": deepcopy(run["forecast"]),
        "validation_errors": ["最终报告引用了不存在的 F999，未通过原文校验。"],
        "recorded_at": "2026-10-08T08:00:00Z",
    }]
    return run


def open_report(page, app_url, run=None):
    run = run or rejected_run()
    routes(page, runs=[run])
    page.goto(app_url + "#/research/run_fixture/report")
    expect(page.locator(".work-header h1")).to_have_text(run["question"]["question"])
    return run


def test_legacy_rejected_report_shows_five_stages_and_recorded_active_time(page, app_url):
    open_report(page, app_url)
    expect(page.locator(".work-status .status-label")).to_have_text("报告待修复")
    expect(page.locator(".work-status")).to_contain_text("5 / 6 阶段")
    expect(page.locator(".stage-progress i.done")).to_have_count(5)
    expect(page.locator(".work-status")).to_contain_text("累计运行 2 分 5 秒")
    expect(page.locator(".directory-services small")).to_contain_text("运行均速 9.9 token/s")
    expect(page.locator(".directory-services small")).to_have_attribute(
        "title", "输出 token 除以已记录阶段的总耗时，包含检索、输入处理和生成")
    expect(page.locator(".report-conclusion")).to_have_text("自动摘要未通过质量检查")
    expect(page.locator(".report-repair")).to_contain_text("最终报告引用了不存在的 F999，未通过原文校验。")
    expect(page.get_by_role("button", name="检查并重试报告 ↗", exact=True)).to_be_enabled()
    for text in ("被拒绝的支持主张不可作为研究结果", "被拒绝的情景结果不可展示", "被拒绝的新增断言不可展示"):
        expect(page.get_by_text(text, exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="导出推演报告 ↗", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="导出推演记录", exact=True)).to_be_visible()


@pytest.mark.parametrize("active_seconds", [None, 0])
def test_legacy_duration_falls_back_to_stage_records_never_wall_clock(page, app_url, active_seconds):
    run = rejected_run()
    if active_seconds is None:
        del run["active_seconds"]
    else:
        run["active_seconds"] = active_seconds
    # The saved wall-clock interval spans weeks; the six stage durations total 125 seconds.
    open_report(page, app_url, run)
    expect(page.locator(".work-status")).to_contain_text("累计运行 2 分 5 秒")
    expect(page.locator(".directory-services small")).to_contain_text("运行均速 10.0 token/s")


def test_accepted_repair_navigates_to_new_child_report_and_polls_to_completion(page, app_url):
    parent = rejected_run(); saved_parent = deepcopy(parent)
    child = deepcopy(parent)
    child.update({"run_id": "run_repair_child", "parent_run_id": parent["run_id"],
                  "report_repair_parent": parent["run_id"], "status": "running", "stage": "forecast",
                  "forecast": None, "forecast_attempts": [], "finished_at": None,
                  "report_repair": {"available": False, "reason": "报告正在生成", "endpoint": None}})
    del child["stage_outputs"]["forecast"]
    completed = deepcopy(child)
    completed.update({"status": "scenario_only", "stage": "done", "active_seconds": 139,
                      "finished_at": "2026-10-08T09:00:00Z"})
    completed["forecast"] = {"status": "scenario_only", "conclusion": "重新核验来源后的修复报告已完成。",
                             "probabilities": None, "supporting": [], "opposing": [], "scenarios": [],
                             "new_information": [], "limitations": ["固定浏览器测试材料"]}
    completed["stage_outputs"]["forecast"] = {"saved": True}
    runs = [parent]
    routes(page, runs=runs)
    captured = {"repairs": [], "child_reads": 0}

    def repair(route):
        captured["repairs"].append((route.request.method, route.request.url))
        runs.insert(0, child)
        route.fulfill(status=202, content_type="application/json", body=json.dumps({"run_id": child["run_id"], "status": "queued"}))

    def child_read(route):
        captured["child_reads"] += 1
        data = child if captured["child_reads"] == 1 else completed
        if captured["child_reads"] > 1:
            runs[0] = completed
        route.fulfill(content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    page.route("**/api/runs/run_fixture/repair-report", repair)
    page.route("**/api/runs/run_repair_child", child_read)
    page.goto(app_url + "#/research/run_fixture/report")
    with page.expect_response("**/api/runs/run_fixture/repair-report") as response:
        page.get_by_role("button", name="检查并重试报告 ↗", exact=True).click()
    assert response.value.status == 202
    expect(page).to_have_url(re.compile(r"#/research/run_repair_child/report$"))
    expect(page.locator(".report-conclusion")).to_have_text("重新核验来源后的修复报告已完成。", timeout=8000)
    assert captured["child_reads"] >= 2
    assert len(captured["repairs"]) == 1 and captured["repairs"][0][0] == "POST"
    assert parent == saved_parent
    expect(page.locator(".work-status")).to_contain_text("6 / 6 阶段")
    expect(page.locator(".work-status")).to_contain_text("累计运行 2 分 19 秒")
    expect(page.get_by_role("link", name="查看原始记录 ↗", exact=True)).to_have_attribute("href", "#/research/run_fixture/report")
    expect(page.get_by_role("link", name="导出推演记录", exact=True)).to_have_attribute("href", "/api/runs/run_repair_child/export?format=json")


def test_repair_422_shows_revalidation_reason_and_keeps_original_run(page, app_url):
    run = rejected_run(); original = deepcopy(run)
    routes(page, runs=[run])
    reason = "证据 F001 原文快照哈希不匹配，需要重新取得来源，不能重试报告。"
    page.route("**/api/runs/run_fixture/repair-report", lambda route: route.fulfill(
        status=422, content_type="application/json", body=json.dumps({"detail": reason}, ensure_ascii=False)))
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(app_url + "#/research/run_fixture/report")
    with page.expect_response("**/api/runs/run_fixture/repair-report") as response:
        page.get_by_role("button", name="检查并重试报告 ↗", exact=True).click()
    assert response.value.status == 422
    expect(page.locator(".app-alert")).to_contain_text(reason)
    expect(page).to_have_url(re.compile(r"#/research/run_fixture/report$"))
    expect(page.locator(".work-status .status-label")).to_have_text("报告待修复")
    expect(page.locator(".work-status")).to_contain_text("5 / 6 阶段")
    expect(page.get_by_role("button", name="检查并重试报告 ↗", exact=True)).to_be_enabled()
    expect(page.get_by_role("link", name="导出推演记录", exact=True)).to_have_attribute("href", "/api/runs/run_fixture/export?format=json")
    assert run == original
    assert not any("/resume" in url or "/run_repair_child" in url for url in requests)


def test_failed_report_json_export_downloads_original_record_without_html(page, app_url):
    run = open_report(page, app_url)
    page.route("**/api/runs/run_fixture/export?format=json", lambda route: route.fulfill(
        status=200, content_type="application/json", headers={"content-disposition": 'attachment; filename="run_fixture.json"'},
        body=json.dumps(run, ensure_ascii=False)))
    expect(page.locator('.report-exports a[href$="format=html"]')).to_have_count(0)
    with page.expect_download() as download:
        page.get_by_role("link", name="导出推演记录", exact=True).click()
    assert download.value.suggested_filename == "run_fixture.json"
    assert json.loads(download.value.path().read_text(encoding="utf-8")) == run


def test_backend_repair_availability_controls_the_action(page, app_url):
    run = rejected_run()
    run["report_repair"] = {"available": False, "reason": "已存在后续修复记录", "endpoint": None}
    open_report(page, app_url, run)
    expect(page.locator(".report-repair")).to_be_visible()
    expect(page.get_by_role("button", name="检查并重试报告 ↗", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="导出推演记录", exact=True)).to_be_visible()


def test_followup_report_links_to_parent_without_repair_metadata(page, app_url):
    run = rejected_run()
    run["parent_run_id"] = "run_original"
    run.pop("report_repair_parent", None)
    open_report(page, app_url, run)
    expect(page.get_by_role("link", name="查看原始研究 ↗", exact=True)).to_have_attribute(
        "href", "#/research/run_original/report")
    expect(page.get_by_role("link", name="查看原始记录 ↗", exact=True)).to_have_count(0)
