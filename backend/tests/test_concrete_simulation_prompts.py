"""Prompt contracts exercise the real graph and carry state changes into named paths."""
from collections import Counter
from copy import deepcopy

from app import graph
from app.agent12_demo import EvidenceFixtureModel
from app.agents.question import analyze_question, finalize_framing
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.schemas import AnalyzeQuestionRequest, QuestionSpec, RunRecord


def test_concrete_prompts_keep_source_scope_and_two_round_state_without_extra_calls(tmp_path):
    request = AnalyzeQuestionRequest(question={
        "question": "推演青岚 V2 的正式发布路径，考虑项目组、测试组、合作方和发布决策方，给出条件和概率。",
        "mode": "scenario", "as_of": DEMO_QUESTION.as_of,
        "resolve_by": DEMO_QUESTION.resolve_by,
    })
    calls = []
    first_change = "待确认→若合作方预留环境且发布方批准，则测试资源锁定；测试组可安排兼容复测"
    second_change = "测试资源锁定→若复测通过则进入发布验收，否则保留延期选择；发布方依据测试组结果作决策"

    class Capture:
        def complete(self, role, payload, schema, instructions, **kwargs):
            calls.append((role, deepcopy(payload)))
            if role == "question12":
                assert payload["question"]["mode"] == "scenario"
                assert "已采取的行动与结果" in instructions
                assert "每条query聚焦一项实际任务或决策变量" in instructions
                assert "保留用户已回答范围中的工具名" in instructions
                assert "先按用户明确列出的研究任务或环节分配" in instructions
                assert "不能替代任务覆盖" in instructions and "通行英文术语" in instructions
                assert "不是新前提" in instructions and "不能反问用户先提供" in instructions
                return schema.model_validate({
                    "proposed_spec": payload["question"],
                    "alternative_directions": ["兼容修复与合作方排期如何共同影响发布决策"],
                    "retrieval_plan": [{"query": "青岚 V2 兼容复测 合作方排期", "purpose": "background"}],
                })

            assert payload["question"]["as_of"] == request.question.as_of.isoformat().replace("+00:00", "Z")
            if role == "evidence12":
                assert "谁已做了什么" in instructions
                assert "无具体缺口时可留空" in instructions and "AI解读而非原文事实" in instructions
                assert "不能代替对已有内容的分析" in instructions
                assert "quote 本身明确表达的事实" in instructions
                return EvidenceFixtureModel().complete(role, payload, schema, instructions)

            output = demo_output(role, "A001" if payload.get("actor", {}).get("id") == "A004"
                                 else payload.get("actor", {}).get("id"), payload.get("round", 1))
            if role == "world":
                assert len(payload["evidence_assessment"]["findings"]) == 3
                assert payload["valid_finding_ids"] == ["F001", "F002", "F003"]
                assert "Round 0当前局势" in instructions and "3–5个" in instructions
                assert "互补决策权限" in instructions and "未具名模拟角色" in instructions
                assert "缺证时写‘未知’" in instructions and "禁止跨任务归因" in instructions
                assert "goal写主体想争取的利益" in instructions
                assert "决策倾向与尚未证实的机制放入assumptions" in instructions
                output["actors"].append({
                    "id": "A004", "name": "发布决策方（模型构造的代表角色）",
                    "goal": "保住稳定版本信誉并决定是否开放下载",
                    "resources": ["正式版发布权限"], "constraints": ["依赖测试组复测和项目组修复"],
                    "visible_evidence_ids": ["E001", "E002"],
                })
                output["relations"] = ["项目组依赖合作方环境复现兼容问题，发布决策方依赖测试组复测结果决定是否开放下载。"]
            elif role == "actor":
                assert len(payload["state"]["variables"]) == 3
                assert set(payload["valid_evidence_ids"]) == set(payload["actor"]["visible_evidence_ids"])
                assert payload["valid_assumption_ids"] == ["H001"]
                assert "动用什么能力或资源" in instructions and "谁可能受益或受损" in instructions
                assert "第二轮依据上一轮state的新变化" in instructions
                assert "不得把‘提出本研究的情景推演’" in instructions
                if payload["round"] == 2:
                    assert payload["state"]["variables"]["partner_slot"] == first_change
                    assert payload["state"]["state_version"] == 1
                    previous = payload["previous_round_actions"]
                    assert {action["id"] for action in previous} == {f"M1-A{i:03}" for i in range(1,5)}
                    assert all({"id","actor_id","action","conditions","rationale_summary"} <= action.keys() for action in previous)
                    assert any(action["actor_id"] == "A004" and "复测" in action["action"] for action in previous)
                    assert payload["previous_round_unresolved"] == ["兼容问题实际修复情况未知"]
                else:
                    assert payload["state"]["variables"]["partner_slot"] == "待确认"
                    assert payload["previous_round_actions"] == []
                    assert payload["previous_round_unresolved"] == []
                if payload["actor"]["id"] == "A004":
                    output.update(action="批准兼容复测后再开放正式版下载",
                                  rationale_summary="发布决策方依赖测试组的复测记录，以此保护稳定版本信誉。",
                                  expected_impact="若复测通过则批准发布；未通过时限制开放下载，避免缺陷扩散。")
            elif role == "environment":
                assert len(payload["actions"]) == 4
                assert all(action["rationale_summary"] and action["expected_impact"] for action in payload["actions"])
                assert set(payload["allowed_variable_keys"]) == set(payload["state"]["variables"])
                assert "原状态→条件成立后的新状态" in instructions
                assert "谁受益或受损" in instructions and "不能凭空生成效果百分比" in instructions
                assert "输出格式不是世界事件或状态变量" in instructions
                assert "前值承接输入state的现值" in instructions
                assert "不重复计算上一轮增量" in instructions
                if payload["round"] == 2:
                    assert payload["state"]["variables"]["partner_slot"] == first_change
                output["state_changes"] = {"partner_slot": first_change if payload["round"] == 1 else second_change}
                output["summary"] = "若合作方提供环境，测试组可推进复测；发布决策方仍依赖复测结果，项目组须先修复兼容问题。"
            elif role == "review":
                assert [step["state_changes"]["partner_slot"] for step in payload["simulation"]] == [first_change, second_change]
                assert "具体主体行动、状态变量或因果连接" in instructions
            elif role == "forecast":
                assert len(payload["actions"]) == 8 and len(payload["simulation"]) == 2
                assert payload["simulation"][1]["state_changes"]["partner_slot"] == second_change
                assert payload["valid_assumption_ids"] == ["H001"]
                assert payload["valid_simulation_ids"] == ["S1", "S2"]
                assert "已模拟行动交互和该终局" in instructions
                assert "重叠时的终局判定顺序" in instructions and "未经校准主观权重" in instructions
                assert "new_information列值得跟踪的具体行动" in instructions
                probabilities = {"完整发布": .5, "缩减发布": .3, "延期": .2}
                definitions = {
                    "完整发布": "目标日前所有计划功能通过复测且正式版开放下载。",
                    "缩减发布": "目标日前仅已通过复测的部分功能开放下载，排除完整发布。",
                    "延期": "目标日前未开放正式版下载，与前两条路径互斥。",
                }
                output.update(probabilities=probabilities, scenarios=list(definitions.values()),
                    scenario_details=[{
                        "name": name, "definition": definitions[name],
                        "conditions": ["核对目标日前正式版下载状态和纳入功能", "核对相应兼容复测记录"],
                        "rationale": "合作方环境与项目组修复共同决定复测能否推进，发布决策方据此选择路径；这是教学条件推演的主观相对分配。",
                        "evidence_ids": ["E001", "E002", "E003"], "assumption_ids": ["H001"], "simulation_ids": ["S1", "S2"],
                    } for name in probabilities])
            return schema.model_validate(output)

    model = Capture()
    frame = finalize_framing(analyze_question(request, None, model), request, None)
    assert frame.premises == [] and len(frame.retrieval_plan) == 1
    question = QuestionSpec.model_validate(frame.proposed_spec.model_dump())
    record = RunRecord(run_id="concrete-prompt-flow", question=question, evidence_mode="import",
                       model="fixture", question_framing=frame)
    result = graph.build_graph(record, demo_evidence(), model, tmp_path).invoke({"question": question.model_dump(mode="json")})
    assert Counter(role for role, _ in calls) == {
        "question12": 1, "evidence12": 1, "world": 1, "actor": 8,
        "environment": 2, "review": 1, "forecast": 1,
    }
    assert result["forecast"]["status"] == "completed"
    assert len(result["forecast"]["scenario_details"]) == 3
    assert sum(result["forecast"]["probabilities"].values()) == 1
    assert result["world"]["variables"]["partner_slot"] == "待确认"
    assert record.evidence_assessment.findings_validated
    assert not record.evidence_assessment.rejected_findings


def test_private_trace_keeps_parents_and_no_progress_or_refused_nodes():
    state = {
        "actions":[{"id":"M2-A001", "actor_id":"A001", "round":2, "action":"拒绝试点",
                    "conditions":["当前人手不足"], "parent_ids":["S1"], "parent_state":1}],
        "simulation":[{"id":"S2", "round":2, "summary":"本轮模拟无进展", "state_changes":{},
                       "conflicts":["对方拒绝试点"], "unresolved":["等待人员安排"],
                       "parent_ids":["M2-A001"], "parent_state":1, "next_state":2}],
    }
    result = graph.trace_for_model(state)
    assert result["actions"] == state["actions"]
    assert result["simulation"] == state["simulation"]
