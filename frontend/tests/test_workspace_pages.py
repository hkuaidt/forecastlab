"""Reading navigation owns a full page, with shareable run routes and intact canvas state."""
from copy import deepcopy
import re
import pytest
from playwright.sync_api import expect
from test_agent12_browser import routes,evidence_run

def long_run(run_id="run_fixture"):
    run=evidence_run()
    run["run_id"]=run_id
    run["question"]["question"]="如何推演一项长期研究计划的发展路径与外部约束？"
    run["world"]={"state_version":1,"summary":"研究计划的主体与约束","variables":{},"relations":[],
        "evidence_refs":["E001"],"assumptions":[],"simulation_branch_reason":"",
        "actors":[{"id":"A001","name":"研究团队","goal":"持续开展研究","resources":["团队与已有研究资料"],
                   "constraints":["经费与外部证据仍需核查"],"information_access":[]}]}
    run["actions"]=[{"id":f"ACT{i:03}","actor_id":"A001","round":i,"parent_state":i,
        "action":f"第{i}轮研究行动","rationale_summary":"依据当前资料持续核查，并记录与假设不符的结果。",
        "expected_impact":"为下一阶段提供可追查资料","evidence_ids":["E001"],"assumption_ids":[]} for i in range(1,16)]
    run["forecast"]={"status":"scenario_only","conclusion":"研究将受到资源、证据质量与协作方式的共同影响。",
        "probabilities":None,"supporting":[{"text":"依据目前材料，仍有多个条件需要持续核查。 "*8,
            "evidence_ids":["E001"],"assumption_ids":[],"simulation_ids":[]} for _ in range(8)],
        "opposing":[],"scenarios":["如果约束持续，研究进度可能放缓。"]*5,
        "new_information":[f"后续资料{i}：核查来源与实际执行情况。" for i in range(12)],
        "limitations":["这是条件推演，不能将假设视为事实。"]}
    run["stage_outputs"]={key:{} for key in ["question","evidence","world","simulation","review","forecast"]}
    return run

@pytest.mark.parametrize("target,label",[("evidence","问题与证据"),("actors","主体与行动"),("report","研究报告")])
@pytest.mark.parametrize("width",[1070,390])
def test_reading_pages_use_full_height_and_scroll_vertically(page,app_url,target,label,width):
    routes(page,runs=[long_run()])
    page.set_viewport_size({"width":width,"height":871})
    page.goto(app_url)
    if width<760:page.get_by_role("button",name="展开目录",exact=True).click()
    page.get_by_role("link",name=re.compile(label)).click()
    expect(page).to_have_url(re.compile("/run_fixture/"+target+"$"))
    expect(page.locator(".canvas-workspace")).to_be_hidden()
    expect(page.locator(".detail-page .reading-header h2")).to_have_text(label)
    if width<760:page.get_by_role("button",name="隐藏目录",exact=True).click()
    panel=page.locator(".detail-page")
    assert panel.bounding_box()["height"]>500
    content=panel.locator(".detail-scroll")
    assert content.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
    assert content.evaluate("el=>getComputedStyle(el).columnCount")=="auto"
    if target in {"actors","report"}:
        assert content.evaluate("el=>el.scrollHeight>el.clientHeight")
        content.evaluate("el=>el.scrollTop=el.scrollHeight")
        assert content.evaluate("el=>el.scrollTop>0")
    if target=="report":
        expect(page.get_by_role("link",name="导出推演记录",exact=True)).to_be_visible()
    page.reload()
    expect(page.locator(".detail-page .reading-header h2")).to_have_text(label)
    expect(page.locator(".work-header h1")).to_have_text(long_run()["question"]["question"])

def test_directory_hide_show_persists_and_releases_width(page,app_url):
    routes(page,runs=[long_run()])
    page.goto(app_url)
    before=page.locator(".desk-main").bounding_box()["width"]
    page.get_by_role("button",name="隐藏目录",exact=True).click()
    expect(page.locator("#research-directory")).to_be_hidden()
    assert page.locator(".desk-main").bounding_box()["width"]>before+150
    page.reload()
    expect(page.get_by_role("button",name="展开目录",exact=True)).to_be_visible()
    expect(page.locator("#research-directory")).to_be_hidden()
    page.get_by_role("button",name="展开目录",exact=True).click()
    expect(page.get_by_role("navigation",name="研究导航")).to_be_visible()

def test_browser_back_keeps_canvas_zoom_and_position(page,app_url):
    routes(page,runs=[long_run()])
    page.goto(app_url)
    page.get_by_role("button",name="放大导图",exact=True).click()
    viewport=page.locator(".map-viewport")
    viewport.evaluate("el=>{el.scrollLeft=220;el.scrollTop=160}")
    position=viewport.evaluate("el=>[el.scrollLeft,el.scrollTop]")
    zoom=page.locator(".zoom-controls output").inner_text()
    page.get_by_role("link",name=re.compile("问题与证据")).click()
    page.get_by_role("link",name=re.compile("主体与行动")).click()
    page.go_back()
    expect(page.locator(".detail-page .reading-header h2")).to_have_text("问题与证据")
    page.go_back()
    expect(page.locator(".canvas-workspace")).to_be_visible()
    expect(page.locator(".zoom-controls output")).to_have_text(zoom)
    assert viewport.evaluate("el=>[el.scrollLeft,el.scrollTop]")==position

def test_deep_link_selects_the_requested_historical_run(page,app_url):
    latest=long_run("run_newer")
    latest["question"]["question"]="最新研究不应覆盖链接指定的历史研究。"
    wanted=long_run()
    routes(page,runs=[latest,wanted])
    page.goto(app_url+"/#/research/run_fixture/report")
    expect(page.locator(".work-header h1")).to_have_text(wanted["question"]["question"])
    expect(page.locator(".detail-page .reading-header h2")).to_have_text("研究报告")
    page.get_by_role("button",name="返回推演画布",exact=True).click()
    expect(page).to_have_url(re.compile("/run_fixture/canvas$"))
    expect(page.locator(".canvas-workspace")).to_be_visible()
