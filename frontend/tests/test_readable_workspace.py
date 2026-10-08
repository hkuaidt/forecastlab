"""Synthetic long records exercise the readable canvas and asynchronous workspace flows."""
from copy import deepcopy
from pathlib import Path
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test_agent12_browser import evidence_run, routes


def detailed_run():
    run = evidence_run()
    run.update({"status": "completed", "stage": "done", "stage_outputs": {key: {} for key in ["question", "evidence", "world", "simulation", "review", "forecast"]}})
    run["question_analysis"] = {"normalized_question": "合成研究计划如何在资源约束下推进？" + "需要逐步核查证据并明确情景边界。" * 20,
                                "search_queries": ["项目资源与执行进度"], "caveats": ["末尾范围说明必须完整展示"]}
    run["world"] = {"state_version": 1, "summary": "主体在预算与协作条件下采取行动。" * 15, "variables": {"研究资源": "资源尚待独立确认"},
                    "relations": ["协作效率影响资料核查"], "evidence_refs": ["E001"], "simulation_branch_reason": "围绕资源是否改善比较条件路径。",
                    "actors": [{"id": "A001", "name": "合成研究团队", "goal": "建立可检查的研究记录", "resources": ["公开资料"], "constraints": ["预算有限"], "visible_evidence_ids": ["E001"]}],
                    "assumptions": [{"id": "H001", "created_by": "world", "parent_ids": ["E001"], "content": "协作资源可能保持稳定", "rationale": "这是用于比较路径的条件假设，不是来源事实。"}]}
    run["actions"] = [{"id": "ACT001", "actor_id": "A001", "round": 1, "parent_state": 1, "action": "开展资料核查", "rationale_summary": "需要验证来源覆盖范围。" * 25 + "行动理由结尾标记",
                       "conditions": ["公开资料可读取", "研究团队仍具备核查时间"], "expected_impact": "将不确定条件明确列入后续研究", "evidence_ids": ["E001"], "assumption_ids": ["H001"]}]
    run["simulation"] = [{"id": "S1", "round": 1, "parent_state": 1, "next_state": 2, "summary": "核查提升可追溯性，但不消除不确定性。", "state_changes": {"资料状态": "从未整理变为可检查"}, "conflicts": ["时间和核查深度之间存在取舍"], "unresolved": ["资源仍待确认"], "evidence_ids": ["E001"], "assumption_ids": ["H001"]}]
    claim = {"text": "若协作条件成立，研究可能逐步推进。", "evidence_ids": ["E001"], "assumption_ids": ["H001"], "simulation_ids": ["S1"]}
    run["review"] = {"status": "passed", "issues": [], "unsupported_claims": [], "missing_evidence": []}
    run["forecast"] = {"status": "completed", "conclusion": "比较稳定推进和资源受限两种路径。", "probabilities": {"稳定推进": .6, "资源受限": .4}, "calibrated": False,
                       "supporting": [claim], "opposing": [dict(claim, text="若资源不足，推进节奏可能放缓。")], "scenarios": ["稳定推进", "资源受限"], "limitations": ["模型主观判断"], "new_information": ["需要更多资源记录"], "key_assumptions": ["H001"],
                       "scenario_details": [{"name": name, "definition": name + "的边界是资源与协作条件是否持续满足。", "conditions": ["资源情况可核查"], "rationale": "依据有限材料比较相对可能性，概率尚未校准。", "evidence_ids": ["E001"], "assumption_ids": ["H001"], "simulation_ids": ["S1"]} for name in ["稳定推进", "资源受限"]]}
    return run


@pytest.mark.parametrize("width", [1070, 390])
def test_canvas_is_readable_and_node_reader_shows_complete_stage_results(page, app_url, width):
    run = detailed_run()
    routes(page, runs=[run])
    page.set_viewport_size({"width": width, "height": 871})
    page.goto(app_url)
    expect(page.locator(".zoom-controls output")).to_have_text("90%" if width == 390 else "100%")
    button = page.get_by_role("button", name="阶段：问题理解", exact=True)
    assert button.bounding_box()["width"] >= 320
    screenshots = Path(__file__).resolve().parents[2] / "docs/agent12/validation-artifacts/screenshots"
    page.screenshot(path=str(screenshots / f"readable-canvas-{width}.png"))
    button.click()
    reader = page.locator(".canvas-workspace > .detail-panel")
    assert reader.bounding_box()["height"] > 400
    assert reader.bounding_box()["width"] >= (350 if width == 390 else 650)
    expect(reader.get_by_text(run["question_analysis"]["normalized_question"], exact=True)).to_be_visible()
    expect(reader.get_by_text("末尾范围说明必须完整展示", exact=True)).to_be_visible()
    assert reader.locator(".detail-scroll").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    page.screenshot(path=str(screenshots / f"readable-question-{width}.png"))
    page.get_by_role("button", name="打开完整页面 ↗", exact=True).click()
    expect(page.locator(".canvas-workspace > .detail-page")).to_be_visible()
    expect(page.get_by_text("末尾范围说明必须完整展示", exact=True)).to_be_visible()
    page.get_by_role("button", name="返回浮层阅读 ↙", exact=True).click()
    page.get_by_role("button", name="收起阅读区", exact=True).click()
    page.get_by_role("button", name="主体行动：合成研究团队", exact=True).click()
    expect(page.locator(".stage-action").get_by_text(run["actions"][0]["rationale_summary"], exact=True)).to_be_visible()
    expect(page.locator(".stage-action").get_by_text("将不确定条件明确列入后续研究", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="行动适用条件", exact=True)).to_be_visible()
    for condition in run["actions"][0]["conditions"]:
        expect(page.locator(".stage-action").get_by_text(condition, exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1070, 390])
@pytest.mark.parametrize("node", ["world", "actor"])
def test_world_and_actor_finding_references_expand_to_saved_quotes_and_sources(page, app_url, width, node):
    run = detailed_run()
    run["world"]["evidence_refs"] = ["F001"]
    run["world"]["actors"][0]["visible_evidence_ids"] = ["F001"]
    source = run["evidence"][0]
    routes(page, runs=[run], passages={"evidence_id": "E001", "text": source["excerpt"], "snapshot_hash": "fixture-hash", "content_truncated": True,
        "passages": [{"paragraph_id": "B000001", "text": source["excerpt"], "start": 0, "end": len(source["excerpt"]), "snapshot_hash": "fixture-hash"}]})
    page.set_viewport_size({"width": width, "height": 871})
    page.goto(app_url)
    page.get_by_role("button", name="阶段：世界建模" if node == "world" else "角色：合成研究团队", exact=True).click()
    finding = page.locator(".stage-reader .finding-reference").first
    finding.locator("summary").click()
    expect(finding.get_by_text("计划延期的迹象", exact=True)).to_be_visible()
    expect(finding).to_contain_text("只有摘要，不能断言结果")
    expect(finding.locator("blockquote")).to_have_text("计划🙂延期")
    expect(page.locator(".stage-reader")).not_to_contain_text("F001 · 来源记录缺失")
    assert page.locator(".detail-scroll").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    if node == "world":
        screenshots = Path(__file__).resolve().parents[2] / "docs/agent12/validation-artifacts/screenshots"
        finding.scroll_into_view_if_needed()
        page.screenshot(path=str(screenshots / f"finding-reference-{width}.png"))
    finding.get_by_role("button", name="E001 · 来源 E001 · 查看已保存摘要 ↗", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_role("dialog")).to_contain_text(source["excerpt"])


@pytest.mark.parametrize("empty_premises", [False, True])
def test_question_reader_keeps_saved_directions_and_retrieval_purpose(page, app_url, empty_premises):
    run = detailed_run()
    if empty_premises:
        run["question_framing"]["premises"] = []
    run["question_framing"]["retrieval_plan"] = [{"id": "T001", "query": "合成计划测试覆盖范围", "purpose": "核对测试范围与研究问题是否一致", "target_premise_ids": [] if empty_premises else ["P001"]}]
    routes(page, runs=[run])
    page.goto(app_url)
    page.get_by_role("button", name="阶段：问题理解", exact=True).click()
    reader = page.locator(".stage-reader")
    expect(reader.get_by_role("heading", name="分析方向", exact=True)).to_be_visible()
    expect(reader.get_by_text("还需核查兼容性", exact=True)).to_be_visible()
    expect(reader.get_by_role("heading", name="合成计划测试覆盖范围", exact=True)).to_be_visible()
    expect(reader).to_contain_text("检索目的 · 核对测试范围与研究问题是否一致")
    if empty_premises:
        expect(reader.get_by_text("未引入额外事实前提。", exact=True)).to_be_visible()
    else:
        expect(reader.get_by_text("关联前提：P001", exact=True)).to_be_visible()
        expect(reader.get_by_text("未引入额外事实前提。", exact=True)).to_have_count(0)


@pytest.mark.parametrize("has_retrieval", [False, True])
def test_question_reader_shows_saved_clarification_without_empty_record_warning(page, app_url, has_retrieval):
    run = detailed_run()
    answer = "范围包含 LLM 猜想、Lean 形式化证明和论文评审。"
    extra = "优先核查公开的可复现结果。"
    run["question_framing"].update({"premises": [], "clarifications": [{"id": "C001", "field": "scope", "question": "研究需要覆盖哪些任务？", "blocking": True, "status": "resolved", "answer": answer}],
        "inputs": [{"input_id": "I001", "kind": "original", "text": run["question"]["question"]}, {"input_id": "I002", "kind": "answer", "text": answer}, {"input_id": "I003", "kind": "answer", "text": extra}],
        "retrieval_plan": [{"id": "T001", "query": "形式化证明的公开进展", "purpose": "initial", "target_premise_ids": []}] if has_retrieval else []})
    run["question_analysis"]["caveats"] = []
    run["question_analysis"]["search_queries"] = []
    run["model_calls"] = [{"request_id": "fixture_timing", "started_at": "2026-10-08T12:00:00Z", "elapsed_seconds": 10, "status": "succeeded", "usage_known": True, "prompt_tokens": 25, "completion_tokens": 180}]
    routes(page, runs=[run])
    page.goto(app_url)
    page.get_by_role("button", name="阶段：问题理解", exact=True).click()
    reader = page.locator(".stage-reader")
    expect(reader.get_by_role("heading", name="研究需要覆盖哪些任务？", exact=True)).to_be_visible()
    expect(reader.get_by_text(answer, exact=True)).to_have_count(1)
    expect(reader.get_by_text(extra, exact=True)).to_be_visible()
    expect(reader).not_to_contain_text("此项尚无保存记录")
    if has_retrieval:
        expect(reader).to_contain_text("检索目的 · 初始取证")
        expect(reader).not_to_contain_text("initial")
    else:
        expect(reader.get_by_role("heading", name="核查方向", exact=True)).to_have_count(0)
    expect(page.locator(".directory-services")).to_contain_text("输出吞吐 18.0 token/s（含输入处理耗时）")


def test_report_and_scenarios_keep_expandable_e_h_s_references(page, app_url):
    run = detailed_run(); routes(page, runs=[run])
    page.goto(app_url + "#/research/run_fixture/report")
    expect(page.locator(".scenario-detail")).to_have_count(2)
    expect(page.locator(".scenario-detail").first).to_contain_text(run["forecast"]["scenario_details"][0]["definition"])
    section = page.locator(".report-section").filter(has=page.get_by_role("heading", name="未选之路与反对依据", exact=True))
    expect(section.locator(".record-references")).to_contain_text("E001")
    section.get_by_text("模型假设 H001 · 协作资源可能保持稳定", exact=True).click()
    expect(section.get_by_text("这是用于比较路径的条件假设，不是来源事实。", exact=True)).to_be_visible()
    section.get_by_text("模拟状态变化 S1 · 第 1 轮", exact=True).click()
    expect(section.get_by_text("核查提升可追溯性，但不消除不确定性。", exact=True)).to_be_visible()


def test_followup_creation_preserves_parent_id(page, app_url):
    captured = routes(page, runs=[detailed_run()])
    page.goto(app_url + "#/research/run_fixture/report")
    page.get_by_role("button", name="创建后续研究 ↗", exact=True).click()
    expect(page.get_by_label("研究问题", exact=True)).to_have_value(detailed_run()["question"]["question"])
    page.get_by_role("button", name="分析问题", exact=True).click()
    page.get_by_label("P001 前提处理").select_option("to_verify")
    page.get_by_role("button", name="确认问题", exact=True).click()
    page.get_by_role("button", name="开始联网推演 →", exact=True).click()
    expect(page.get_by_role("dialog", name="新建事件研究")).to_have_count(0)
    assert captured["runs"][0]["parent_run_id"] == "run_fixture"


def test_history_pages_summaries_then_loads_selected_full_record(page, app_url):
    template = detailed_run()
    rows = [dict(template, run_id=f"run_page_{i}", question=dict(template["question"], question=f"合成研究 {i}")) for i in range(21)]
    routes(page, runs=[])
    seen = []
    def listing(route):
        query = parse_qs(urlsplit(route.request.url).query); seen.append(query)
        offset = int(query.get("offset", [0])[0]); limit = int(query.get("limit", [20])[0])
        summaries = [{key: r[key] for key in ["run_id", "question", "status", "stage", "started_at", "finished_at", "parent_run_id", "model"]} for r in rows[offset:offset+limit]]
        route.fulfill(content_type="application/json", body=json.dumps(summaries))
    page.route("**/api/runs?*", listing)
    page.route("**/api/runs/run_page_*", lambda route: route.fulfill(content_type="application/json", body=json.dumps(rows[int(route.request.url.rsplit('_', 1)[1])], ensure_ascii=False)))
    page.goto(app_url)
    expect(page.locator(".work-header h1")).to_have_text("合成研究 0")
    page.get_by_role("button", name="切换研究 ↗", exact=True).click()
    expect(page.locator(".history-row")).to_have_count(20)
    page.get_by_role("button", name="加载更早记录", exact=True).click()
    expect(page.locator(".history-row")).to_have_count(21)
    page.locator(".history-row").filter(has=page.get_by_text("合成研究 20", exact=True)).click()
    expect(page.locator(".work-header h1")).to_have_text("合成研究 20")
    assert all(query["summary"] == ["true"] for query in seen)
    assert seen[-1]["offset"] == ["20"]


def test_cancel_running_run_retains_records_and_health_does_not_claim_ready(page, app_url):
    run = detailed_run(); run.update({"status": "running", "stage": "simulation", "forecast": None})
    routes(page, runs=[run])
    page.route("**/api/health", lambda route: route.fulfill(content_type="application/json", body=json.dumps({"ok": True, "model": "fixture", "model_configured": True, "search_configured": True, "model_ready": False, "search_ready": None})))
    cancelled = dict(run, status="cancelled", stage="cancelled")
    page.route("**/api/runs/run_fixture/cancel", lambda route: route.fulfill(content_type="application/json", body=json.dumps(cancelled)))
    page.goto(app_url)
    expect(page.locator(".directory-services")).to_contain_text("模型暂不可用")
    expect(page.locator(".directory-services")).to_contain_text("搜索已配置 · 待请求验证")
    with page.expect_response("**/api/runs/run_fixture/cancel") as response:
        page.get_by_role("button", name="停止运行", exact=True).click()
    assert response.value.request.method == "POST"
    expect(page.locator(".work-status .status-label")).to_have_text("已停止")
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="阶段：世界建模", exact=True)).to_have_count(1)


@pytest.mark.parametrize("action", ["resume", "repair-report"])
def test_late_run_action_does_not_override_new_selection(page, app_url, action):
    original = detailed_run(); original.update({"status": "failed", "stage": "failed", "failed_stage": "simulation", "forecast": None})
    original["stage_outputs"].pop("forecast")
    if action == "repair-report":
        original["failed_stage"] = "forecast"
        original["report_repair"] = {"available": True, "reason": "固定回归", "endpoint": "/api/runs/run_fixture/repair-report"}
    other = deepcopy(detailed_run()); other["run_id"] = "run_other"; other["question"]["question"] = "切换后的独立研究"
    routes(page, runs=[original, other])
    page.route("**/api/runs/run_other", lambda route: route.fulfill(content_type="application/json", body=json.dumps(other)))
    pending = []
    page.route("**/api/runs/run_fixture/" + action, lambda route: pending.append(route))
    page.goto(app_url + "#/research/run_fixture/report")
    page.get_by_role("button", name="从失败阶段继续 ↗" if action == "resume" else "检查并重试报告 ↗", exact=True).click()
    page.get_by_role("button", name="切换研究 ↗", exact=True).click()
    page.locator(".history-row").filter(has=page.get_by_text("切换后的独立研究", exact=True)).click()
    expect(page.locator(".work-header h1")).to_have_text("切换后的独立研究")
    pending[0].fulfill(status=202, content_type="application/json", body=json.dumps({"run_id": "run_fixture", "status": "queued"}))
    expect(page).to_have_url(app_url + "/#/research/run_other/report")
    page.wait_for_timeout(250)
    expect(page.locator(".work-header h1")).to_have_text("切换后的独立研究")
