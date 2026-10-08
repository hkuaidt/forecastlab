"""Saved facts, actions, state changes and scenario boundaries drive canvas cards."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_agent12_browser import routes, SOURCE_TEXT
from test_readable_workspace import detailed_run


def content_run():
    run = detailed_run()
    run["evidence_assessment"]["findings_validated"] = True
    run["world"]["summary"] = "公开记录显示研究已进入核查阶段。团队需要在既定预算中先核对资料，再决定扩大验证范围；资料可获得性会影响后续协作节奏。"
    run["world"]["variables"] = {"资料覆盖": "已取得初步记录，尚需核对完整范围", "验证能力": "团队能够复核部分资料，独立复现仍待安排", "协作资源": "资源尚未增加，下一轮依赖现有人员与时间"}
    run["actions"][0]["rationale_summary"] = "先核对资料的版本和适用范围，能够识别哪些判断有直接依据，避免把摘要中的阶段性结果扩大为全面完成。"
    run["actions"][0]["expected_impact"] = "形成逐项核对清单，让后续团队能够复查判断及其限制。"
    run["simulation"][0]["state_changes"] = {"资料覆盖": "新增版本对照，但完整范围仍有缺口", "验证能力": "核查清单已形成，独立复现尚未完成", "协作资源": "核查占用现有人力，扩大研究暂缓"}
    run["review"]["issues"] = [
        {"severity": "low", "claim": "只有搜索摘要", "explanation": "未取得完整正文；发布时间未知", "affected_ids": ["E001"]},
        {"severity": "high", "claim": "来源中的完成范围相互矛盾", "explanation": "E001 描述部分测试，E002 描述全部测试，范围尚未统一。", "affected_ids": ["E001", "E002"]},
        {"severity": "medium", "claim": "只有摘要", "explanation": "E001 将阶段性测试写成全面完成，这个具体范围差异需要核查。", "affected_ids": ["E001"]},
    ]
    run["forecast"]["scenario_details"][0]["definition"] = "稳定推进指团队维持现有协作，同时逐步扩大可复查资料的覆盖范围。每次新增材料都保留原始引句与适用限制，阶段性测试不能直接视为全面完成；这一情景仍取决于人力和公开资料能否持续获得。"
    run["forecast"]["scenario_details"][0]["rationale"] = "现有记录支持继续开展资料核查，但没有证明资源已经扩张。相对概率依赖协作稳定和来源覆盖这两个条件；如果条件未满足，研究进度仍可能转入资源受限情景。"
    return run


@pytest.mark.parametrize("width", [1070, 390])
def test_canvas_cards_project_saved_findings_states_actions_and_scenarios(page, app_url, width):
    run = content_run()
    routes(page, runs=[run])
    page.set_viewport_size({"width": width, "height": 871})
    page.goto(app_url)
    if width == 390:
        expect(page.locator(".map-minimap svg")).to_be_hidden()
        page.get_by_role("button", name="展开全图导航", exact=True).click()
        expect(page.locator(".map-minimap svg")).to_be_visible()
        page.get_by_role("button", name="收起全图导航", exact=True).click()
        expect(page.locator(".map-minimap svg")).to_be_hidden()
    question = page.get_by_role("button", name="阶段：问题理解", exact=True)
    assert question.locator("..").bounding_box()["height"] < 260
    finding = page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True)
    expect(finding).to_contain_text("计划🙂延期")
    expect(finding).to_contain_text("只有摘要，不能断言结果")
    expect(page.get_by_role("button", name="来源：来源 E001", exact=True)).to_have_count(0)
    world = page.get_by_role("button", name="阶段：世界建模", exact=True)
    expect(world.locator("h3")).to_have_text("初始局势 · Round 0")
    expect(world.locator(".node-facts > div")).to_have_count(3)
    for value in run["world"]["variables"].values():
        expect(world).to_contain_text(value)
    actor = page.get_by_role("button", name="角色：合成研究团队", exact=True)
    expect(actor.locator("h3")).to_have_text("合成研究团队")
    expect(actor).to_contain_text("初始角色 · Round 0")
    expect(actor).to_contain_text("建立可检查的研究记录")
    expect(actor).not_to_contain_text("开展资料核查")
    action = page.get_by_role("button", name="主体行动：合成研究团队", exact=True)
    expect(action.locator("h3")).to_have_text("开展资料核查")
    expect(action).to_contain_text("合成研究团队 · 第 1 轮")
    expect(action).to_contain_text(run["actions"][0]["rationale_summary"])
    expect(action).to_contain_text(run["actions"][0]["expected_impact"])
    step = page.get_by_role("button", name="轮次：第 1 轮演化", exact=True)
    expect(step.locator(".node-facts > div")).to_have_count(3)
    scenario = page.get_by_role("button", name="结果分支：稳定推进", exact=True)
    expect(scenario).to_contain_text("60.0%")
    expect(scenario).to_contain_text(run["forecast"]["scenario_details"][0]["definition"])
    expect(scenario).to_contain_text(run["forecast"]["scenario_details"][0]["conditions"][0])
    expect(scenario).to_contain_text(run["forecast"]["scenario_details"][0]["rationale"])
    screenshots = Path(__file__).resolve().parents[2] / "docs/agent12/validation-artifacts/screenshots"
    for label, card in [("facts", finding), ("state", world), ("action", action), ("scenario", scenario)]:
        card.scroll_into_view_if_needed()
        assert card.evaluate("el => [...el.querySelectorAll('h3,p,.node-facts,span')].every(child => child.getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1)")
        page.screenshot(path=str(screenshots / f"content-canvas-{label}-{width}.png"))
    scenario.click()
    reader = page.locator(".stage-reader")
    expect(reader.get_by_text(run["forecast"]["scenario_details"][0]["definition"], exact=True)).to_be_visible()
    expect(reader.get_by_text(run["forecast"]["scenario_details"][0]["rationale"], exact=True)).to_be_visible()
    assert reader.evaluate("el => el.scrollWidth <= el.clientWidth + 1")


def test_source_quality_is_grouped_without_hiding_concrete_issues_or_saved_quotes(page, app_url):
    run = content_run()
    routes(page, runs=[run], passages={"evidence_id": "E001", "text": SOURCE_TEXT, "snapshot_hash": "fixture-hash", "content_truncated": True,
        "passages": [{"paragraph_id": "B000001", "text": SOURCE_TEXT, "start": 0, "end": len(SOURCE_TEXT), "snapshot_hash": "fixture-hash"}]})
    page.goto(app_url)
    expect(page.get_by_role("button", name="审查意见：只有搜索摘要", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="审查意见：来源中的完成范围相互矛盾", exact=True)).to_have_count(1)
    expect(page.get_by_role("button", name="审查意见：只有摘要", exact=True)).to_have_count(1)
    page.get_by_role("button", name="资料限制：资料质量与限制", exact=True).click()
    expect(page.locator(".stage-reader")).to_contain_text("未取得完整正文；发布时间未知")
    page.get_by_role("button", name="收起阅读区", exact=True).click()
    page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True).click()
    expect(page.locator(".stage-reader blockquote")).to_have_text("计划🙂延期")
    expect(page.locator(".stage-reader")).to_contain_text("只有摘要，不能断言结果")
    page.get_by_role("button", name="查看已保存摘要 · E001 ↗", exact=True).click()
    expect(page.get_by_role("dialog").locator("mark")).to_have_text("计划🙂延期")


@pytest.mark.parametrize("placeholder", ["待核查", "待验证", "尚待核查。"])
def test_unvalidated_findings_fall_back_to_saved_source_content(page, app_url, placeholder):
    run = content_run()
    run["evidence_assessment"]["findings_validated"] = False
    run["evidence_assessment"]["summary"] = ""
    run["evidence"][0]["claim"] = placeholder
    routes(page, runs=[run])
    page.goto(app_url)
    expect(page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True)).to_have_count(0)
    source = page.get_by_role("button", name="来源：来源 E001", exact=True)
    expect(source).to_contain_text(SOURCE_TEXT)
    expect(source).to_contain_text("E001 · 搜索摘要")
    expect(page.get_by_role("button", name="阶段：证据核查", exact=True)).to_contain_text(SOURCE_TEXT)


def test_finding_source_reader_uses_complete_snapshot_beyond_excerpt_limit(page, app_url):
    run = content_run()
    complete_text = SOURCE_TEXT + "完整来源材料中的可复查记录。" * 1100 + "完整快照尾部标记"
    assert len(complete_text) > 12000
    run["evidence"][0]["excerpt"] = complete_text[:12000]
    routes(page, runs=[run], passages={"evidence_id": "E001", "text": complete_text, "snapshot_hash": "fixture-hash", "content_truncated": False,
        "passages": [{"paragraph_id": "B000001", "text": complete_text, "start": 0, "end": len(complete_text), "snapshot_hash": "fixture-hash"}]})
    page.goto(app_url)
    page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True).click()
    page.get_by_role("button", name="查看已保存摘要 · E001 ↗", exact=True).click()
    expect(page.get_by_role("dialog").locator("mark")).to_have_text("计划🙂延期")
    expect(page.get_by_role("dialog").locator(".source-body")).to_have_text(complete_text)


def test_saved_round_actions_are_visible_before_state_evolution_finishes(page, app_url):
    run = content_run()
    run.update(status="running", stage="simulation", simulation=[], forecast=None)
    routes(page, runs=[run])
    page.goto(app_url)
    expect(page.get_by_role("button", name="主体行动：合成研究团队", exact=True)).to_have_count(1)
    step = page.get_by_role("button", name="轮次：第 1 轮演化", exact=True)
    expect(step).to_contain_text("等待状态演化结果")
    expect(step.locator("..").locator(".node-bar")).to_contain_text("运行中")
    expect(page.get_by_role("button", name="结果分支：稳定推进", exact=True)).to_have_count(0)
    step.click()
    expect(page.locator(".stage-reader")).to_contain_text("状态演化结果尚未保存")
    expect(page.locator(".stage-action")).to_contain_text("开展资料核查")
