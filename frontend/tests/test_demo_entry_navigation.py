"""New research starts blank and never launches a model without confirmation."""
import pytest
from playwright.sync_api import expect

@pytest.mark.parametrize("viewport", [{"width": 1724, "height": 864}, {"width": 390, "height": 844}])
@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_new_research_is_blank_and_does_not_start_run(page, app_url, viewport, motion):
    from test_agent12_browser import routes
    routes(page)
    page.set_viewport_size(viewport)
    page.emulate_media(reduced_motion=motion)
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method == "POST" else None)
    page.goto(app_url)
    entry = page.get_by_role("button", name="新建研究 ＋", exact=True)
    entry.focus()
    page.keyboard.press("Enter")
    question = page.get_by_label("研究问题", exact=True)
    expect(question).to_have_value("")
    expect(question).to_be_focused()
    expect(question).to_be_in_viewport()
    expect(page.get_by_role("button", name="开始联网推演 →", exact=True)).to_be_disabled()
    assert writes == []
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(entry).to_be_focused()
