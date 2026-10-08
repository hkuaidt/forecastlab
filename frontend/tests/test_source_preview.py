"""Canvas source previews use saved passages with the actual runtime form enums."""
import pytest
from playwright.sync_api import expect

from test_agent12_browser import routes
from test_canvas_content import content_run


@pytest.mark.parametrize("metadata,label,preview_label,button", [
    ({"id": "E001", "title": "Advancing mathematics by guiding human intuition with AI | Nature", "content_kind": "snippet", "source_type": "snippet_only", "source_kind": "unknown"}, "搜索摘要", "搜索摘要选段 · 展开保存摘要", "查看已保存摘要 ↗"),
    ({"id": "E002", "title": "Introduction", "content_kind": "body", "source_type": "secondary", "source_kind": "unknown"}, "正文", "原文选段 · 展开保存全文", "查看已保存正文 ↗"),
    ({"id": "E003", "title": "Generative AI policies for journals", "content_kind": "body", "source_type": "secondary", "source_kind": "unknown"}, "正文", "原文选段 · 展开保存全文", "查看已保存正文 ↗"),
])
def test_runtime_source_metadata_uses_passage_preview_and_retains_snapshot(page, app_url, metadata, label, preview_label, button):
    # Only the public source titles and enum metadata come from run_81dc91b22cb5.
    # Body and passage below are synthetic; no real research content is retained.
    run = content_run()
    run["evidence_assessment"]["findings"] = []
    run["evidence_assessment"]["summary"] = ""
    passage = "这是登记快照中实际保存的选段🙂，不是模型重新写的摘要。" * 30
    prefix = "页面开头导航。" * 40
    full_text = prefix + passage + "完整材料中其他段落。" * 1400 + "保存全文末尾标记"
    source = {**run["evidence"][0], **metadata, "claim": "待核查", "excerpt": full_text[:12000], "content_truncated": False,
        "passages": [{"paragraph_id": "B000002", "text": passage, "start": len(prefix), "end": len(prefix)+len(passage), "snapshot_hash": "fixture-hash"}]}
    run["evidence"] = [source]
    routes(page, runs=[run], passages={"evidence_id": source["id"], "text": full_text, "snapshot_hash": "fixture-hash", "content_truncated": False, "passages": []})
    page.goto(app_url)
    card = page.get_by_role("button", name="来源："+source["title"], exact=True)
    expect(card.locator(".node-source-labels")).to_have_text(source["id"]+" · "+label)
    expect(card.locator(".node-subtitle")).to_have_text(preview_label)
    expect(card.locator("p")).to_have_text(passage[:420])
    expect(card).not_to_contain_text("页面开头导航")
    assert card.evaluate("el => [...el.querySelector('p').textContent].length") <= 420
    assert card.locator("..").bounding_box()["height"] < 430
    expect(page.get_by_role("button", name="阶段：证据核查", exact=True).locator("p")).to_have_text(passage[:420])
    card.click()
    page.get_by_role("button", name=button, exact=True).click()
    expect(page.get_by_role("dialog").locator(".source-body")).to_have_text(full_text)


def test_missing_passages_quote_bounded_saved_excerpt_without_inventing_preview(page, app_url):
    run = content_run()
    run["evidence_assessment"]["findings"] = []
    source = run["evidence"][0]
    source.update({"content_kind": "body", "source_type": "secondary", "claim": "模型论点不应代替本卡的保存原文", "excerpt": "现有原文🙂"*300, "passages": []})
    routes(page, runs=[run])
    page.goto(app_url)
    card = page.get_by_role("button", name="来源："+source["title"], exact=True)
    expect(card.locator("p")).to_have_text(source["excerpt"][:420])
    expect(card).not_to_contain_text(source["claim"])
