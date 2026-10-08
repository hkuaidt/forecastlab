"""Actor and environment prompts keep research deliverables outside the simulated world."""
from app import graph
from app.demo import DEMO_QUESTION, demo_evidence, demo_output
from app.schemas import RunRecord


def test_actual_actor_environment_calls_keep_reality_role_and_payload(tmp_path):
    calls=[]
    class Capture:
        def complete(self,role,payload,schema,instructions):
            if role=="actor":
                calls.append((role,payload["round"]))
                assert payload["actor"]["goal"] and payload["actor"]["resources"]
                assert payload["state"] and payload["question"]["question"]==DEMO_QUESTION.question
                assert "现实主体" in instructions and "可执行的现实行动" in instructions
                assert "不得把‘提出本研究的情景推演’" in instructions
            elif role=="environment":
                calls.append((role,payload["round"]))
                assert len(payload["actions"])==3 and "allowed_variable_keys" in payload
                assert "输出格式不是世界事件或状态变量" in instructions
                assert "不能据此制造现实变化" in instructions
            return schema.model_validate(demo_output(role,payload.get("actor",{}).get("id"),payload.get("round",1)))
    record=RunRecord(run_id="role_boundary",question=DEMO_QUESTION,evidence_mode="import",model="fixture")
    graph.build_graph(record,demo_evidence(),Capture(),tmp_path).invoke({"question":DEMO_QUESTION.model_dump(mode="json")})
    assert sum(role=="actor" for role,_ in calls)==6
    assert [(role,round_) for role,round_ in calls if role=="environment"]==[("environment",1),("environment",2)]
