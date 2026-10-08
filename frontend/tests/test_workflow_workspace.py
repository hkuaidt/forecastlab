"""Saved stages, evidence navigation, zoom and narrow reading remain usable."""
import pytest
from playwright.sync_api import expect

@pytest.mark.parametrize('width',[1440,390])
def test_workflow_navigation_and_mobile_detail(page,app_url,width):
    from test_agent12_browser import routes, evidence_run
    routes(page,runs=[evidence_run()])
    page.set_viewport_size({'width':width,'height':900})
    page.goto(app_url)
    expect(page.locator('.work-header h1')).to_have_text(evidence_run()["question"]["question"])
    expect(page.locator('.tree-node').filter(has=page.get_by_text('阶段',exact=True))).to_have_count(6)
    page.get_by_role('button',name='阶段：证据核查',exact=True).press('Enter')
    expect(page.locator('.reading-header h2')).to_have_text('证据核查')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    before=page.locator('.zoom-controls output').inner_text()
    page.get_by_role('button',name='放大导图').click()
    assert page.locator('.zoom-controls output').inner_text()!=before
    page.get_by_role('button',name='收起阅读区',exact=True).click()
    expect(page.locator('.detail-panel')).to_have_count(0)
    page.get_by_role('button',name='折叠证据核查分支').click()
    expect(page.get_by_role('button',name='来源：来源 E001',exact=True)).to_have_count(0)
    page.get_by_role('button',name='展开证据核查分支').click()
    expect(page.get_by_role('button',name='来源：来源 E001',exact=True)).to_have_count(1)


def test_rejected_model_summary_is_not_presented_as_a_research_conclusion(page, app_url):
    from copy import deepcopy
    from test_agent12_browser import RUN, routes
    run = deepcopy(RUN)
    run["forecast"] = {"status": "scenario_only", "conclusion": "自动摘要未通过校验", "probabilities": None,
        "supporting": [{"text": "不应展示的支持主张", "evidence_ids": [], "assumption_ids": [], "simulation_ids": []}],
        "opposing": [], "scenarios": ["影响达到55%"], "new_information": ["不应展示的来源断言"],
        "limitations": ["自动报告的完整性校验未通过"]}
    routes(page, runs=[run]); page.goto(app_url)
    page.get_by_role("button", name="阶段：预测报告", exact=True).click()
    expect(page.get_by_text("不应展示的支持主张", exact=True)).to_have_count(0)
    expect(page.get_by_text("影响达到55%", exact=True)).to_have_count(0)
    expect(page.get_by_text("不应展示的来源断言", exact=True)).to_have_count(0)
    expect(page.get_by_text("自动摘要未通过质量检查，未作为研究结论展示。", exact=False)).to_be_visible()
