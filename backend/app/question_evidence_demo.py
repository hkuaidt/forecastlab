"""Strictly allow-listed classroom fixtures, not a fallback model for arbitrary input."""
from __future__ import annotations
import json
from . import config
from .compat import canonical_demo_case_id
from .demo import demo_evidence
from .schemas import FramingCandidate, RetrievalResult, QuestionDraft
from .provenance import save_snapshot, split_passages, select_passages


def classroom_case():
    return json.loads((config.ROOT / "examples/question-evidence/classroom-case.json").read_text(encoding="utf-8"))


def validate_demo_request(request):
    case = classroom_case()
    if canonical_demo_case_id(request.demo_case_id) != case["case_id"]:
        raise ValueError("未知教学案例")
    canonical = QuestionDraft.model_validate(case["question"])
    if request.question.question not in {canonical.question, case["normalized_question"]}:
        raise ValueError("此教学模式只接受固定案例问题；任意输入请切换真实模式")
    for name in ("mode", "as_of", "resolve_by", "resolution_rule", "resolution_source", "user_assumptions"):
        if getattr(request.question, name) != getattr(canonical, name):
            raise ValueError("教学案例的日期、规则与范围固定；不对任意输入返回固定答案")
    if any(a.answer not in case["allowed_answers"] for a in request.answers):
        raise ValueError("固定教学回答请选择：" + " / ".join(case["allowed_answers"]))


class QuestionFixtureModel:
    actual_model = "fixture:question-evidence-demo"
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete(self, role, payload, schema, instructions, **kwargs):
        if role != "question_framing":
            raise ValueError("固定问题模型仅用于已验证教学问题")
        case = classroom_case()
        original = next(i for i in payload["inputs"] if i["kind"] == "original")
        leading = original["text"] == case["question"]["question"]
        answered = any(i["kind"] == "answer" and i["text"] in case["allowed_answers"] for i in payload["inputs"])
        spec = {**payload["question"], "question": case["normalized_question"]}
        premises = []
        if leading:
            premises = [{"content": "全部测试已经完成", "origin": "user_explicit", "source_input_id": original["input_id"],
                         "original_span": "已经完成全部测试", "rationale": "需要核查测试类型、日期与完成记录。"},
                        {"content": "测试完成就能保证按期发布", "origin": "model_inferred", "source_input_id": original["input_id"],
                         "original_span": "所以V2肯定能在11月15日前发布", "rationale": "从措辞中识别出的因果前提，需用户确认，不是已知事实。"}]
        return schema.model_validate({"proposed_spec": spec, "premises": premises,
            "clarifications": [{"field": "release_kind", "question": "这里的发布是可下载的正式版，还是测试版？", "blocking": True}] if leading and not answered else [],
            "alternative_directions": ["核查兼容性阻塞、合作方排期是否影响发布路径。"],
            "retrieval_plan": [{"query": "青岚 V2 官方路线图", "purpose": "initial", "premise_indexes": [1] if leading else []},
                               {"query": "青岚 V2 兼容测试阻塞", "purpose": "challenge", "premise_indexes": [0,1] if leading else []},
                               {"query": "青岚 托管测试资源排期", "purpose": "alternative", "premise_indexes": [1] if leading else []}]})


def demo_materials(question, framing, data_dir):
    if canonical_demo_case_id(framing.demo_case_id) != classroom_case()["case_id"]:
        raise ValueError("未知教学案例")
    items = []
    for evidence in demo_evidence():
        snapshot = save_snapshot(evidence.excerpt, {"provider": "fixture", "case_id": framing.demo_case_id,
            "title": evidence.title, "synthetic": True}, data_dir)
        evidence.snapshot_path = snapshot.snapshot_path
        evidence.snapshot_hash = snapshot.snapshot_hash
        evidence.retrieved_at = snapshot.stored_at
        evidence.source_kind = "unknown"
        evidence.content_kind = "imported_excerpt"
        evidence.availability = "synthetic"
        evidence.date_status = "synthetic"
        evidence.source_group_basis = "教学虚构来源，不代表真实独立证据"
        evidence.passages = select_passages(split_passages(snapshot), [question.question])
        items.append(evidence)
    return RetrievalResult(evidence=items)


class EvidenceFixtureModel:
    actual_model = "fixture:question-evidence-demo"
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete(self, role, payload, schema, instructions, **kwargs):
        if role != "evidence_assessment":
            raise ValueError("固定证据模型仅用于教学材料")
        active = [p["id"] for p in payload["question_framing"]["premises"]]
        findings = []
        rows = [("E001", "计划在 11 月中旬发布 V2", "计划在 11 月中旬发布 V2。", "background"),
                ("E002", "两个高优先级兼容问题", "存在两个高优先级兼容问题。", "challenges"),
                ("E003", "可提供额外测试资源", "可提供额外测试资源。", "alternative")]
        for eid, quote, claim, relation in rows:
            evidence = next(e for e in payload["evidence"] if e["id"] == eid)
            passage = next(p for p in evidence["passages"] if quote in p["text"])
            findings.append({"target_premise_ids": active, "claim": claim, "relation": relation,
                "citations": [{"evidence_id": eid, "snapshot_hash": evidence["snapshot_hash"], "paragraph_id": passage["paragraph_id"], "quote": quote}],
                "limitation": "固定教学响应，只演示结构校验和来源追查，不代表真实模型效果。"})
        return schema.model_validate({"summary": "教学材料同时包含发布目标、兼容问题和资源条件。",
            "findings": findings, "conflicts": [{"issue": "发布目标与测试问题之间的张力", "finding_indexes": [0,1],
                "scope_comparison": "路线图是计划，测试记录描述当前问题，并非同一事实的直接矛盾。",
                "status": "resolved", "explanation": "保留两类材料，不用目标日期覆盖实际测试状态。"}],
            "gaps": [{"missing": "缺少后续复测和合作方最终排期的教学材料", "cause": "not_found"}]})
