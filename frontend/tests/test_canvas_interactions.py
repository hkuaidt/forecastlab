"""Real browser input coverage for the canvas's mouse, keyboard and touch rules."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_agent12_browser import evidence_run, routes


def open_canvas(page, app_url):
    routes(page, runs=[evidence_run()])
    page.goto(app_url)
    canvas = page.locator(".map-viewport")
    expect(canvas).to_be_visible()
    expect(page.get_by_role("button", name="阶段：问题理解", exact=True)).to_have_count(1)
    # Give both axes enough scroll range for a meaningful drag/anchor check.
    for _ in range(6):
        if int(page.locator(".zoom-controls output").inner_text().rstrip("%")) >= 90:
            break
        page.get_by_role("button", name="放大导图", exact=True).click()
    canvas.evaluate("el => el.scrollTo({left: 0, top: 0})")
    return canvas


def position(canvas):
    return canvas.evaluate("el => ({left: el.scrollLeft, top: el.scrollTop})")


@pytest.mark.parametrize("target", ["blank", "node", "collapse"])
def test_right_drag_pans_without_opening_or_collapsing_nodes(page, app_url, target):
    canvas = open_canvas(page, app_url)
    before_count = page.locator(".tree-node").count()
    if target == "blank":
        bounds = canvas.bounding_box()
        x, y = bounds["x"] + 140, bounds["y"] + 140
    else:
        button = page.get_by_role("button", name="阶段：问题理解" if target == "node" else "折叠问题理解分支", exact=True)
        button.scroll_into_view_if_needed()
        bounds = button.bounding_box()
        x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
    page.evaluate("window.contextCancelled = []; window.addEventListener('contextmenu', e => window.contextCancelled.push(e.defaultPrevented))")
    before = position(canvas)
    page.mouse.move(x, y)
    page.mouse.down(button="right")
    expect(canvas).to_have_class("map-viewport is-panning")
    page.mouse.move(x - 75, y - 55, steps=5)
    page.mouse.up(button="right")
    expect(canvas).to_have_class("map-viewport")
    after = position(canvas)
    assert after["left"] > before["left"] + 60
    assert after["top"] > before["top"] + 40
    expect(page.locator(".tree-node.selected")).to_have_count(0)
    assert page.locator(".tree-node").count() == before_count
    assert page.evaluate("window.contextCancelled.length > 0 && window.contextCancelled.every(Boolean)")


def test_left_drag_does_not_pan_and_left_click_still_inspects(page, app_url):
    canvas = open_canvas(page, app_url)
    bounds = canvas.bounding_box()
    x, y = bounds["x"] + 140, bounds["y"] + 140
    before = position(canvas)
    page.mouse.move(x, y)
    page.mouse.down(button="left")
    page.mouse.move(x - 75, y - 55, steps=5)
    page.mouse.up(button="left")
    assert position(canvas) == before
    expect(canvas).to_have_class("map-viewport")
    node = page.get_by_role("button", name="阶段：问题理解", exact=True)
    node.click()
    expect(page.locator('.node-content[aria-label="阶段：问题理解"]')).to_have_attribute("aria-pressed", "true")


def test_right_drag_capture_ends_when_released_outside_canvas(page, app_url):
    canvas = open_canvas(page, app_url)
    bounds = canvas.bounding_box()
    x, y = bounds["x"] + 140, bounds["y"] + 140
    page.mouse.move(x, y)
    page.mouse.down(button="right")
    page.mouse.move(bounds["x"] - 50, bounds["y"] - 30, steps=5)
    page.mouse.up(button="right")
    expect(canvas).to_have_class("map-viewport")
    after = position(canvas)
    page.mouse.move(x, y)
    assert position(canvas) == after


def test_plain_wheel_zooms_about_pointer_and_does_not_scroll_outside_canvas(page, app_url):
    canvas = open_canvas(page, app_url)
    canvas.evaluate("el => el.scrollTo({left: 200, top: 160})")
    bounds = canvas.bounding_box()
    point = {"x": bounds["x"] + 370, "y": bounds["y"] + 270}
    def world_point():
        return page.locator(".map-plane").evaluate("""(el, p) => {
            const r = el.getBoundingClientRect(), z = new DOMMatrix(getComputedStyle(el).transform).a;
            return {x: (p.x-r.left)/z, y: (p.y-r.top)/z};
        }""", point)
    before = world_point()
    old_zoom = page.locator(".zoom-controls output").inner_text()
    page.mouse.move(point["x"], point["y"])
    page.mouse.wheel(0, -100)
    expect(page.locator(".zoom-controls output")).not_to_have_text(old_zoom)
    after = world_point()
    assert abs(after["x"] - before["x"]) < 2
    assert abs(after["y"] - before["y"]) < 2
    assert page.evaluate("window.scrollY") == 0
    zoomed = page.locator(".zoom-controls output").inner_text()
    # A real scrollable region outside the canvas verifies that wheel capture
    # stays local, including after the pointer has left the canvas.
    page.evaluate("""() => {
        const box = document.createElement('div'); box.id = 'outside-scroll-check';
        box.style.cssText = 'position:fixed;left:0;top:0;width:100px;height:100px;overflow:auto;z-index:99999;background:white';
        box.innerHTML = '<div style="height:800px">Scroll outside canvas</div>'; document.body.append(box);
    }""")
    page.mouse.move(50, 50)
    page.mouse.wheel(0, 200)
    page.wait_for_function("document.getElementById('outside-scroll-check').scrollTop > 0")
    expect(page.locator(".zoom-controls output")).to_have_text(zoomed)
    page.locator("#outside-scroll-check").evaluate("el => el.remove()")


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_node_keyboard_activation_is_preserved(page, app_url, key):
    open_canvas(page, app_url)
    node = page.get_by_role("button", name="阶段：证据核查", exact=True)
    node.press(key)
    expect(page.locator('.node-content[aria-label="阶段：证据核查"]')).to_have_attribute("aria-pressed", "true")


def test_zoom_fit_minimap_and_collapse_controls_remain_usable(page, app_url):
    canvas = open_canvas(page, app_url)
    output = page.locator(".zoom-controls output")
    before = output.inner_text()
    page.get_by_role("button", name="放大导图", exact=True).press("Enter")
    expect(output).not_to_have_text(before)
    page.get_by_role("button", name="定位STAGE 6", exact=True).click()
    assert position(canvas)["left"] > 0
    page.get_by_role("button", name="适应画布", exact=True).click()
    assert position(canvas) == {"left": 0, "top": 0}
    page.get_by_role("button", name="折叠证据核查分支", exact=True).click()
    expect(page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True)).to_have_count(0)
    page.get_by_role("button", name="展开证据核查分支", exact=True).click()
    expect(page.get_by_role("button", name="证据发现：计划延期的迹象", exact=True)).to_have_count(1)


def test_touch_can_pan_natively_and_tap_nodes(browser, app_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page = context.new_page()
    try:
        canvas = open_canvas(page, app_url)
        bounds = canvas.bounding_box()
        x, y = bounds["x"] + 130, bounds["y"] + min(220, bounds["height"] - 50)
        session = context.new_cdp_session(page)
        session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        for offset in range(15, 151, 15):
            session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y - offset}]})
            page.wait_for_timeout(20)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_function("document.querySelector('.map-viewport').scrollTop > 40")
        expect(canvas).to_have_class("map-viewport")
        screenshot = Path(__file__).resolve().parents[2] / "docs/agent12/validation-artifacts/screenshots/canvas-touch-pan.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot))
        node = page.get_by_role("button", name="阶段：问题理解", exact=True)
        node.tap()
        expect(page.locator('.node-content[aria-label="阶段：问题理解"]')).to_have_attribute("aria-pressed", "true")
    finally:
        context.close()
