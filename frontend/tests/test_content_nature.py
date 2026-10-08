"""Content nature stays explicit across cards, direct readers and source forms."""
from copy import deepcopy
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_agent12_browser import routes
from test_canvas_content import content_run


def mixed_run():
    run = content_run()
    first, second = run["evidence"]
    first.pop("content_kind")  # Legacy records may only carry source_type.
    first["content_truncated"] = False
    second["content_truncated"] = False
    third = {**deepcopy(second), "id": "E003", "content_kind": "imported_excerpt", "title": "导入来源", "excerpt": "导入的实际片段"}
    fourth = {**deepcopy(second), "id": "E004", "content_kind": "unknown", "source_type": "primary", "title": "形式未知来源", "excerpt": "获取形式没有保存"}
    run["evidence"].extend([third, fourth])
    finding = run["evidence_assessment"]["findings"][0]
    finding["citations"].append(deepcopy(run["evidence_assessment"]["findings"][1]["citations"][0]))
    for source in (third, fourth):
        finding["citations"].append({"evidence_id": source["id"], "snapshot_hash": "fixture-hash", "paragraph_id": "B000001", "quote": source["excerpt"], "start": 0, "end": len(source["excerpt"])})
    return run


@pytest.mark.parametrize("width", [1070, 390])
def test_mixed_finding_lists_every_source_form_and_keeps_full_reading(page, app_url, width):
    run = mixed_run()
    routes(page, runs=[run])
    page.set_viewport_size({"width": width, "height": 871})
    page.goto(app_url)
    card = page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True)
    expect(card.locator("..").locator(".node-bar")).to_contain_text("事实释义 · 模型提取")
    labels = ["E001 · 搜索摘要", "E002 · 正文", "E003 · 导入节选", "E004 · 获取形式未标明"]
    expect(card.locator(".node-source-labels small")).to_have_text(labels)
    card.scroll_into_view_if_needed()
    assert card.evaluate("el => [...el.querySelectorAll('.node-source-labels small')].every(child => child.getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1)")
    directory = Path(__file__).resolve().parents[2] / "docs/agent12/validation-artifacts/screenshots"
    page.screenshot(path=str(directory / f"nature-finding-{width}.png"))
    card.click()
    reader = page.locator(".stage-reader")
    expect(reader.get_by_role("heading", name="事实释义 · 模型提取", exact=True)).to_be_visible()
    for label in labels:
        expect(reader.get_by_text(label, exact=True)).to_be_visible()
    for citation in run["evidence_assessment"]["findings"][0]["citations"]:
        expect(reader.get_by_text(citation["quote"], exact=True)).to_be_visible()
    for label in ["查看已保存摘要 · E001 ↗", "查看已保存正文 · E002 ↗", "查看已保存节选 · E003 ↗", "查看已保存材料 · E004 ↗"]:
        expect(reader.get_by_role("button", name=label, exact=True)).to_be_visible()
    expect(reader).to_contain_text("只有摘要，不能断言结果")
    page.get_by_role("button", name="打开完整页面 ↗", exact=True).click()
    expect(page.locator(".detail-page .stage-reader")).to_contain_text("E004 · 获取形式未标明")
    assert reader.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    page.screenshot(path=str(directory / f"nature-finding-reader-{width}.png"))


@pytest.mark.parametrize("width", [1070, 390])
def test_direct_action_step_and_scenario_readers_keep_conditional_nature(page, app_url, width):
    run = content_run()
    routes(page, runs=[run])
    page.set_viewport_size({"width": width, "height": 871})
    page.goto(app_url)
    world = page.get_by_role("button", name="阶段：世界建模", exact=True)
    expect(world.locator("..").locator(".node-bar")).to_contain_text("初始状态 · 含模型假设")
    world.click()
    expect(page.locator(".stage-reader")).to_contain_text("初始状态 · 含模型假设")
    page.get_by_role("button", name="收起阅读区", exact=True).click()
    directory = Path(__file__).resolve().parents[2] / "docs/agent12/validation-artifacts/screenshots"
    for title, nature, shot in [("主体行动：合成研究团队", "模拟行动", "action"), ("轮次：第 1 轮演化", "模拟状态变化", "step")]:
        card = page.get_by_role("button", name=title, exact=True)
        expect(card.locator("..").locator(".node-bar")).to_contain_text(nature)
        expect(card.locator("..").locator(".node-bar small")).to_have_text("已保存")
        card.click()
        reader = page.locator(".stage-reader")
        expect(reader).to_contain_text(nature)
        expect(reader).to_contain_text("以下是条件模拟，不代表已经发生的事实")
        expect(reader).to_contain_text("E001")  # An E reference does not change its nature.
        page.get_by_role("button", name="打开完整页面 ↗", exact=True).click()
        expect(page.locator(".detail-page .stage-reader")).to_contain_text(nature)
        assert reader.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        page.screenshot(path=str(directory / f"nature-{shot}-reader-{width}.png"))
        page.get_by_role("button", name="返回推演画布", exact=True).click()
    scene = page.get_by_role("button", name="结果分支：稳定推进", exact=True)
    expect(scene.locator("..").locator(".node-bar")).to_contain_text("条件情景 · 主观权重未经校准")
    scene.click()
    expect(page.locator(".stage-reader")).to_contain_text("条件情景 · 主观权重未经校准")
    expect(page.locator(".stage-reader")).to_contain_text("60.0% · 主观概率 · 未经校准")
    page.goto(app_url + "/#/research/" + run["run_id"] + "/report")
    expect(page.locator(".detail-page")).to_contain_text("条件情景 · 主观权重未经校准")
    expect(page.locator(".probability-list small")).to_have_text("主观概率 · 未经校准")


def test_saved_body_is_complete_and_source_link_does_not_claim_a_live_fetch(page, app_url):
    run = mixed_run()
    body = "测试已完成" + "可核查的长正文片段。" * 1500 + "真实保存快照的末尾标记"
    run["evidence"][1]["excerpt"] = body[:12000]
    passages = {"evidence_id": "E002", "text": body, "snapshot_hash": "fixture-hash", "content_truncated": False, "passages": []}
    routes(page, runs=[run], passages=passages)
    page.goto(app_url)
    page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True).click()
    page.get_by_role("button", name="查看已保存正文 · E002 ↗", exact=True).click()
    dialog = page.get_by_role("dialog", name="来源原文")
    expect(dialog.locator(".source-body")).to_have_text(body)
    expect(dialog.locator("mark")).to_have_text("测试已完成")
    expect(dialog.get_by_role("link", name="打开来源网页 ↗", exact=True)).to_have_attribute("href", run["evidence"][1]["source_url"])
    expect(dialog).to_contain_text("E002 · 正文")


def test_legacy_sources_without_findings_keep_ambiguous_forms_ambiguous(page, app_url):
    run = mixed_run()
    run["evidence_assessment"]["findings"] = []
    routes(page, runs=[run])
    page.goto(app_url)
    expect(page.get_by_role("button", name="来源：来源 E001", exact=True)).to_contain_text("E001 · 搜索摘要")
    unknown = page.get_by_role("button", name="来源：形式未知来源", exact=True)
    expect(unknown).to_contain_text("获取形式未标明")
    expect(unknown).not_to_contain_text("正文")
    unknown.click()
    expect(page.locator(".stage-reader")).to_contain_text("获取形式未标明")
    expect(page.get_by_role("button", name="查看已保存材料 ↗", exact=True)).to_be_visible()
