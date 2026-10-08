"""Unaccepted proposals and unmeasured pilots need no fabricated state improvement."""
import pytest

from app import graph
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.schemas import RunRecord


@pytest.mark.parametrize("accepted", [False, True])
def test_real_graph_keeps_expectation_separate_from_realized_or_measured_state(tmp_path, accepted):
    actor_states = []
    environment_rounds = []
    trial_after = "在双方同意的模拟路径下，试点已获同意但尚未实施"

    class Model:
        def complete(self, role, payload, schema, instructions):
            actor = payload.get("actor", {}).get("id")
            round_number = payload.get("round", 1)
            output = demo_output(role, actor, round_number)
            if role == "world":
                output["actors"] = output["actors"][:2]
                output["variables"] = {"trial": "尚未同意试点", "efficiency": "尚未测量"}
                output["assumptions"] = [{
                    "id":"H001", "created_by":"model", "parent_ids":["E002"],
                    "content":"如果测试组接受并完成试点，项目组预期复测会更快",
                    "rationale":"这是待检验的效果假设，不能覆盖测试组的实际拒绝或资源约束"}]
            elif role == "actor":
                assert "expected_impact是主体预期，不是本轮已实现的结果" in instructions
                actor_states.append((round_number, payload["state"]["variables"].copy()))
                output.update(
                    action="邀请测试组开展辅助复测试点" if actor == "A001" else (
                        "同意后续开展试点，但本轮尚未执行或测量" if accepted else "拒绝采用试点，当前人员仅够完成既有回归"),
                    expected_impact="预期在测试组配合并完成试点后缩短复测时间",
                    rationale_summary="项目组期待缩短复测时间，但测试组须依据人员约束作决定。",
                    assumption_ids=["H001"],
                )
            elif role == "environment":
                assert len(payload["actions"]) == 2
                assert "预期" in payload["actions"][0]["expected_impact"]
                assert payload["state"]["variables"]["efficiency"] == "尚未测量"
                assert "不能直接抄为state_changes" in instructions
                assert "由输入state、对方action或明确H" in instructions
                assert "H不能覆盖对方明确拒绝" in instructions
                assert "允许state_changes为空" in instructions
                assert "对方同意试点只代表同意" in instructions
                assert "不能写成已提高效率" in instructions
                environment_rounds.append(round_number)
                output.update(
                    summary="若双方后续完成试点，才可能检验效率；本轮未测量任何效率变化。",
                    state_changes={"trial":trial_after} if accepted else {},
                    conflicts=[] if accepted else ["项目组希望试点，但测试组明确拒绝且人员不足。"],
                    unresolved=["试点未执行，缺少前后复测耗时记录。"] if accepted else ["测试组未接受试点，H001不能代替对方配合。"],
                )
            return schema.model_validate(output)

    record = RunRecord(run_id="realization-fixture", question=DEMO_QUESTION,
                       evidence_mode="import", model="fixture")
    result = graph.build_graph(record, demo_evidence(), Model(), tmp_path).invoke({
        "question":DEMO_QUESTION.model_dump(mode="json")})
    assert environment_rounds == [1, 2]
    second_round = [variables for round_number, variables in actor_states if round_number == 2]
    assert len(second_round) == 2
    assert all(variables["efficiency"] == "尚未测量" for variables in second_round)
    assert all(variables["trial"] == (trial_after if accepted else "尚未同意试点") for variables in second_round)
    assert all("efficiency" not in step["state_changes"] for step in result["simulation"])
    if not accepted:
        assert all(step["state_changes"] == {} and step["conflicts"] for step in result["simulation"])
