import json
import re
from copy import deepcopy
from playwright.sync_api import expect

QUESTION = {"question": "青岚项目能否按期发布正式版？", "mode": "scenario", "as_of": "2026-09-30T08:00:00Z",
    "resolve_by": None, "resolution_rule": "", "resolution_source": None, "user_assumptions": []}
FRAME = {"schema_version": 1, "draft_id": "draft_fixture", "revision": 1, "raw_question": QUESTION["question"],
    "proposed_spec": QUESTION, "inputs": [], "clarifications": [], "premises": [{"id": "P001", "content": "测试已完成",
    "origin": "user_explicit", "source_input_id": "I001", "original_span": "测试已完成", "rationale": "需要核查实际范围",
    "user_review": "pending", "treatment": "to_verify", "replaces_id": None}], "alternative_directions": ["还需核查兼容性"],
    "retrieval_plan": [], "status": "ready_for_confirmation", "analysis_record": {"validation_mode": "fixture"}, "demo_case_id": None}
RUN = {"run_id": "run_fixture", "question": {"id": "Q1", "outcomes": ["是","否"], **QUESTION}, "question_origin": "confirmed",
    "question_framing": FRAME, "confirmation_id": "confirm_fixture", "parent_run_id": None, "question_version": 1,
    "evidence_mode": "import", "demo": False, "status": "scenario_only", "stage": "done", "stage_outputs": {},
    "question_analysis": None, "evidence": [], "evidence_assessment": None, "world": None, "actions": [], "simulation": [],
    "review": None, "forecast": None, "settlement": None, "model": "fixture", "started_at": QUESTION["as_of"], "finished_at": QUESTION["as_of"],
    "usage": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}, "errors": []}


def routes(page, *, missing_key=False, runs=None, framing=None, passages=None):
    captured = {"runs": [], "confirmations": [], "analyses": []}
    frame = deepcopy(framing or FRAME)
    def handler(route):
        path = route.request.url.split("/api", 1)[1].split("?", 1)[0]
        method = route.request.method
        status = 200
        if path == "/health":
            data = {"ok": True, "model_configured": not missing_key, "search_configured": True, "model": "fixture"}
        elif path == "/examples": data = {"presets": []}
        elif path == "/settlements/summary": data = {"settled_count": 0, "scored_count": 0, "average_brier": None}
        elif path == "/questions/analyze":
            captured["analyses"].append(route.request.post_data_json)
            data = frame if not missing_key else {"detail": "未配置模型密钥，不能生成真实分析"}
            status = 200 if not missing_key else 503
        elif path.endswith("/confirm"):
            captured["confirmations"].append(route.request.post_data_json)
            data = {"confirmation_id": "confirm_fixture", "framing": frame, "question": RUN["question"], "revision": frame["revision"]}
        elif path == "/questions/draft_fixture": data = {"framing": frame, "confirmation": None}
        elif path == "/runs" and method == "POST":
            captured["runs"].append(route.request.post_data_json)
            data = {"run_id": "run_fixture", "status": "queued"}; status = 202
        elif path == "/runs": data = deepcopy(runs or [])
        elif path.endswith("/passages"): data = passages
        elif path == "/runs/run_fixture": data = deepcopy((runs or [RUN])[0])
        else: data = {"detail": "unknown test route"}; status = 404
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))
    page.route("**/api/**", handler)
    return captured


def prepare(page, app_url):
    page.goto(app_url)
    page.get_by_role("button", name="＋ 新建研究", exact=True).click()
    page.get_by_label("研究问题", exact=True).fill(QUESTION["question"])
    page.get_by_label("分析方式").select_option("scenario")
    page.get_by_role("button", name="分析问题", exact=True).click()


def test_question_confirmation_flow(page, app_url):
    captured = routes(page)
    prepare(page, app_url)
    expect(page.get_by_role("button", name="确认问题", exact=True)).to_be_disabled()
    expect(page.get_by_role("option", name="保留并核查，不当作事实", exact=True)).to_have_count(1)
    page.get_by_label("P001 前提处理").select_option("to_verify")
    page.get_by_role("button", name="确认问题", exact=True).click()
    expect(page.get_by_text("问题已确认。联网搜索、核查和推演将使用这一版本。", exact=True)).to_be_visible()
    page.get_by_role("button", name=re.compile("^开始联网推演")).click()
    expect(page.get_by_role("dialog",name="新建事件研究")).to_have_count(0)
    expect(page.locator(".work-header h1")).to_have_text(QUESTION["question"])
    assert captured["runs"][0]["confirmation_id"] == "confirm_fixture"
    assert "question" not in captured["runs"][0]


def test_edit_invalidates_confirmation(page, app_url):
    routes(page); prepare(page, app_url)
    page.get_by_label("P001 前提处理").select_option("to_verify")
    page.get_by_role("button", name="确认问题", exact=True).click()
    expect(page.get_by_text("问题已确认。联网搜索、核查和推演将使用这一版本。", exact=True)).to_be_visible()
    page.locator(".research-dialog textarea").first.fill("修改了范围，另一个版本能否发布？")
    expect(page.get_by_role("button", name=re.compile("^开始联网推演"))).to_be_disabled()
    expect(page.get_by_text("内容已修改，请重新分析后确认。", exact=True)).to_be_visible()


def test_new_research_does_not_restore_offline_draft(page, app_url):
    captured = routes(page)
    page.add_init_script("localStorage.setItem('forecastlab.agent12.draft_id','draft_fixture')")
    page.goto(app_url)
    page.get_by_role("button", name="＋ 新建研究", exact=True).click()
    expect(page.get_by_label("研究问题", exact=True)).to_have_value("")
    expect(page.get_by_text("需要核查实际范围", exact=True)).to_have_count(0)
    assert captured["analyses"] == []
    assert page.evaluate("localStorage.getItem('forecastlab.question-framing.draft_id')") == "draft_fixture"
    assert page.evaluate("localStorage.getItem('forecastlab.agent12.draft_id')") is None


def test_missing_key_does_not_create_fake_analysis(page, app_url):
    routes(page, missing_key=True); prepare(page, app_url)
    expect(page.get_by_text("未配置模型密钥，不能生成真实分析", exact=True)).to_be_visible()
    expect(page.get_by_text("需要核查实际范围", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile("^开始联网推演"))).to_be_disabled()


SOURCE_TEXT = "说明😀：计划🙂延期，不代表项目取消。"

def evidence_run():
    run = deepcopy(RUN)
    run["question_framing"]["premises"].append({**run["question_framing"]["premises"][0], "id": "P002", "content": "另一项前提"})
    def evidence(eid, content, kind):
        return {"id": eid, "source_url": "https://example.org/" + "long-path-"*20, "file_id": None, "title": "来源 " + eid,
            "publisher": "样例来源", "published_at": None, "updated_at": None, "retrieved_at": QUESTION["as_of"], "event_at": None,
            "excerpt": content, "claim": "", "snapshot_path": "sources-v1/server-owned.json", "content_hash": "abc", "snapshot_hash": "fixture-hash",
            "source_type": "snippet_only" if kind == "snippet" else "secondary", "source_group": "root-1", "date_status": "unknown", "conflict_group": None,
            "content_kind": kind, "content_truncated": True, "source_kind": "unknown", "source_kind_basis": "无法确定是否一手来源",
            "source_group_basis": "明确转载标记，尚待人工核查", "possible_same_source": [], "aliases": [],
            "date_basis": {"retrieved_at": "后端实际取得时间"}, "availability": "unverified", "event_status": "planned"}
    run["evidence"] = [evidence("E001", SOURCE_TEXT, "snippet"), evidence("E002", "测试已完成", "body")]
    c1 = {"evidence_id": "E001", "snapshot_hash": "fixture-hash", "paragraph_id": "B000001", "quote": "计划🙂延期", "start": 4, "end": 9}
    c2 = {"evidence_id": "E002", "snapshot_hash": "fixture-hash", "paragraph_id": "B000001", "quote": "测试已完成", "start": 0, "end": 5}
    run["evidence_assessment"] = {"summary": "固定样例中的两项发现", "evidence_ids": ["E001", "E002"], "conflicts": [], "gaps": [],
        "findings": [{"id": "F001", "target_premise_ids": ["P001"], "claim": "计划延期的迹象", "relation": "challenges", "citations": [c1], "limitation": "只有摘要，不能断言结果"},
                     {"id": "F002", "target_premise_ids": ["P002"], "claim": "另一项有效判断", "relation": "supports", "citations": [c2], "limitation": "测试范围有待核查"}],
        "conflict_details": [], "gap_details": [], "retrieval_log": [], "exclusions": [],
        "rejected_findings": [{"candidate": {"claim": "不应进入有效结果的伪造内容"}, "reason": "引文未出现在原文"}]}
    return run


def inspect_view(page, app_url, run=None):
    run = run or evidence_run()
    routes(page, runs=[run], passages={"evidence_id": "E001", "text": SOURCE_TEXT, "snapshot_hash": "fixture-hash", "content_truncated": True,
        "passages": [{"paragraph_id": "B000001", "text": SOURCE_TEXT, "start": 0, "end": len(SOURCE_TEXT), "snapshot_hash": "fixture-hash"}]})
    page.goto(app_url)
    page.get_by_role("button", name=re.compile("问题与证据")).click()


def test_filter_findings_by_premise_and_relation(page, app_url):
    inspect_view(page, app_url)
    page.get_by_label("按前提筛选").select_option("P001")
    expect(page.get_by_test_id("valid-findings").get_by_text("计划延期的迹象", exact=True)).to_be_visible()
    expect(page.get_by_test_id("valid-findings").get_by_text("另一项有效判断", exact=True)).to_have_count(0)
    page.get_by_label("按前提筛选").select_option("")
    page.get_by_label("按关系筛选").select_option("supports")
    expect(page.get_by_test_id("valid-findings").get_by_text("另一项有效判断", exact=True)).to_be_visible()
    expect(page.get_by_test_id("valid-findings").get_by_text("计划延期的迹象", exact=True)).to_have_count(0)


def test_quote_highlight_keeps_emoji_offsets(page, app_url):
    inspect_view(page, app_url)
    page.get_by_role("button", name="查看 E001 原文", exact=True).click()
    expect(page.locator("mark")).to_have_text("计划🙂延期")
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("button", name="查看 E001 原文", exact=True)).to_be_focused()


def test_rejected_findings_not_in_valid_results(page, app_url):
    inspect_view(page, app_url)
    expect(page.get_by_test_id("valid-findings").get_by_text("不应进入有效结果的伪造内容", exact=True)).to_have_count(0)
    expect(page.get_by_text("校验未通过", exact=True)).to_be_visible()
    page.get_by_text("校验未通过", exact=True).click()
    expect(page.get_by_text("引文未出现在原文", exact=True)).to_be_visible()


def test_legacy_record_has_no_fabricated_framing(page, app_url):
    run = deepcopy(RUN); run["question_framing"] = None; run["question_origin"] = "legacy_direct"
    inspect_view(page, app_url, run)
    expect(page.get_by_text("旧版记录未包含问题理解/逐项发现", exact=True)).to_be_visible()
    expect(page.get_by_test_id("valid-findings")).to_have_count(0)


def test_source_limitations_visible(page, app_url):
    inspect_view(page, app_url)
    expect(page.get_by_text("只有搜索摘要，未取得正文", exact=True).first).to_be_visible()
    page.get_by_role("button", name="查看 E001 原文", exact=True).click()
    expect(page.get_by_role("dialog").get_by_text("发布时间未知", exact=True)).to_be_visible()
    expect(page.get_by_role("dialog").get_by_text("正文已截断，不是完整原文", exact=True)).to_be_visible()
    expect(page.get_by_text("明确转载标记，尚待人工核查", exact=True)).to_be_visible()


def test_mobile_findings_and_drawer_fit_viewport(page, app_url):
    page.set_viewport_size({"width": 390, "height": 844})
    inspect_view(page, app_url)
    page.get_by_role("button", name="查看 E001 原文", exact=True).click()
    expect(page.locator("mark")).to_have_text("计划🙂延期")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="关闭来源原文", exact=True).click()
    expect(page.get_by_role("button", name="查看 E001 原文", exact=True)).to_be_focused()



def test_real_backend_saved_run_and_source_inspection(page, app_url):
    # Populate only the isolated test backend; production has no demo entry.
    response = page.request.post(app_url + "/api/runs", data={"question": RUN["question"], "evidence_mode": "demo", "evidence": []})
    assert response.status == 202
    run_id = response.json()["run_id"]
    import time
    for _ in range(100):
        record = page.request.get(app_url + "/api/runs/" + run_id).json()
        if record["status"] not in ("queued", "running"):
            break
        time.sleep(.1)
    assert record["status"] in ("completed", "scenario_only", "insufficient_evidence")
    page.goto(app_url)
    expect(page.locator(".work-header h1")).to_have_text(record["question"]["question"])
    page.get_by_role("button", name=re.compile("问题与证据")).click()
    page.locator(".source-short").first.click()
    expect(page.get_by_role("dialog", name="来源原文")).to_be_visible()
    expect(page.get_by_role("dialog").get_by_text("教学虚构材料", exact=True)).to_be_visible()


def test_source_hash_mismatch_blocks_highlight(page, app_url):
    run = evidence_run()
    routes(page, runs=[run], passages={"evidence_id": "E001", "text": SOURCE_TEXT,
        "snapshot_hash": "changed-hash", "content_truncated": True, "passages": []})
    page.goto(app_url)
    page.get_by_role("button", name=re.compile("问题与证据")).click()
    page.get_by_role("button", name="查看 E001 原文", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("原文哈希或引用位置不匹配，不能高亮引用。")
    expect(page.locator("mark")).to_have_count(0)


def test_research_supplement_requires_reviewed_status(page, app_url):
    run = evidence_run()
    routes(page, runs=[run])
    candidate = {"model": "fixture", "generated_at": "2026-10-08", "quality_status": "candidate",
        "parts": [{"name": "analysis", "request_id": "request_fixture", "sections": [{"title": "尚未核验的专题", "paragraphs": ["候选正文不能展示"], "source_ids": ["B001"]}]}],
        "sources": [], "calls": []}
    page.route("**/assets/research-run_fixture.json", lambda route: route.fulfill(content_type="application/json", body=json.dumps(candidate)))
    page.goto(app_url)
    with page.expect_response("**/assets/research-run_fixture.json"):
        page.get_by_role("button", name=re.compile("研究报告")).click()
    expect(page.get_by_text("候选正文不能展示", exact=True)).to_have_count(0)
    candidate["quality_status"] = "reviewed"
    page.get_by_role("button", name=re.compile("问题与证据")).click()
    with page.expect_response("**/assets/research-run_fixture.json"):
        page.get_by_role("button", name=re.compile("研究报告")).click()
    expect(page.get_by_text("候选正文不能展示", exact=True)).to_be_visible()


def test_quality_profile_and_conflict_source_links_are_in_existing_evidence_flow(page, app_url):
    run = evidence_run()
    assessment = run["evidence_assessment"]
    assessment["quality_profile"] = {
        "source_count": 2, "source_group_count": 1, "body_source_count": 1,
        "snippet_only_count": 1, "primary_label_count": 0, "unknown_publication_count": 2,
        "truncated_count": 2, "suspected_same_source_count": 0, "merged_alias_count": 0,
        "search_success_count": 1, "search_empty_count": 0, "search_failure_count": 1,
        "excluded_count": 1, "validated_finding_count": 2, "rejected_finding_count": 1,
        "unresolved_conflict_count": 1,
        "warnings": ["来源组不代表真实独立性", "只有摘要需保留限制"],
    }
    assessment["conflict_details"] = [{
        "issue": "测试口径冲突", "finding_ids": ["F001", "F002"],
        "scope_comparison": "测试计划与测试完成状态口径不同",
        "status": "unresolved", "explanation": "仍需比对原文",
    }]
    inspect_view(page, app_url, run)
    quality = page.get_by_label("证据质量概览")
    expect(quality).to_be_visible()
    expect(quality.get_by_text("有效来源", exact=True)).to_be_visible()
    expect(quality.get_by_text("来源组", exact=True)).to_be_visible()
    quality.get_by_text("其他取证指标", exact=True).click()
    expect(quality.get_by_text("标记一手来源", exact=False)).to_be_visible()
    quality.get_by_text("来源与证据限制（2）", exact=True).click()
    expect(quality.get_by_text("来源组不代表真实独立性", exact=True)).to_be_visible()
    expect(page.get_by_text("测试口径冲突", exact=True)).to_be_visible()
    page.get_by_role("button", name="E001 · 来源 E001").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.locator("mark")).to_have_text("计划🙂延期")


def test_draft_key_migration_preserves_current_value(page, app_url):
    routes(page)
    page.add_init_script("""localStorage.setItem('forecastlab.agent12.draft_id','old-draft');
        localStorage.setItem('forecastlab.question-framing.draft_id','current-draft');""")
    page.goto(app_url)
    page.get_by_role('button', name='＋ 新建研究', exact=True).click()
    assert page.evaluate("localStorage.getItem('forecastlab.question-framing.draft_id')") == 'current-draft'
    assert page.evaluate("localStorage.getItem('forecastlab.agent12.draft_id')") is None
    expect(page.get_by_label('研究问题', exact=True)).to_have_value('')
