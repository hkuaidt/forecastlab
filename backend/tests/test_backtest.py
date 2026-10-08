"""Historical forecasts must use only information available at the cutoff."""
import json
from pathlib import Path

import pytest
from app.graph import available_at_cutoff, build_graph, market_price_context, repair_forecast, validate_forecast
from app.schemas import Assumption, Claim, Forecast, ImportedEvidence, QuestionSpec, Review, RunRecord, WorldState
from app.sources import normalize_import


EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "python-313-2024-backtest-evidence.json"


def historical_question() -> QuestionSpec:
    return QuestionSpec.model_validate({
        "question": "Python 官方是否会在 UTC 2024 年 10 月 1 日 23:59 前发布 Python 3.13.0 正式稳定版？",
        "as_of": "2024-09-10T00:00:00Z",
        "resolve_by": "2024-10-01T23:59:00Z",
        "resolution_rule": "以 Python.org 的 Python 3.13.0 正式版发布日期为准；rc、beta 不算。",
        "resolution_source": "https://www.python.org/downloads/",
    })


def historical_evidence(question: QuestionSpec):
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    items = [ImportedEvidence.model_validate(item) for item in payload["evidence"]]
    return normalize_import(items, question)


class BacktestModel:
    def __init__(self, can_estimate: bool):
        self.can_estimate = can_estimate
        self.calls = []

    def complete(self, role, payload, schema, instructions):
        self.calls.append((role, payload))
        evidence_ids = [item["id"] for item in payload.get("evidence", [])]
        if role == "question":
            output = {"normalized_question": payload["question"]["question"], "search_queries": ["Python 3.13 release schedule"]}
        elif role == "evidence":
            output = {
                "summary": "截止日前官方计划于 10 月 1 日发布，但关键缺陷可能导致延期。",
                "evidence_ids": evidence_ids,
                "gaps": ["截止日之后是否发现关键缺陷仍未知。"],
            }
        elif role == "world":
            output = {
                "summary": "截止日仍处于候选版阶段。",
                "variables": {"release_status": "尚未正式发布"},
                "actors": [],
                "evidence_refs": evidence_ids,
                "assumptions": [],
            }
        elif role == "review":
            output = {
                "status": "blocked",
                "issues": [{
                    "severity": "high",
                    "claim": "缺少未来的发布结果",
                    "explanation": "截止日之后是否真的按期发布未知。",
                    "affected_ids": evidence_ids[:1],
                }],
                "unsupported_claims": ["2024 年 10 月 1 日已经正式发布"],
                "missing_evidence": ["2024 年 9 月 10 日之后的实际发布记录"],
            }
        elif role == "evidence_audit":
            output = {
                "can_estimate": self.can_estimate,
                "blocking_reasons": [] if self.can_estimate else ["没有可用的截止日前证据"],
            }
        elif role == "forecast":
            output = {
                "status": "completed",
                "conclusion": "按期发布有可能，但关键缺陷可能导致延期。",
                "probabilities": {"是": 0.65, "否": 0.35},
                "supporting": [{"text": "官方发布日程指向 10 月 1 日。", "evidence_ids": evidence_ids[:1]}] if evidence_ids else [],
                "opposing": [{"text": "官方承认关键缺陷可能导致延期。", "evidence_ids": evidence_ids[:1]}] if evidence_ids else [],
                "limitations": ["只基于截止日前资料，概率未经校准。"],
            }
        else:
            raise AssertionError(f"unexpected model role: {role}")
        return schema.model_validate(output)


def test_historical_forecast_estimates_without_future_result_evidence():
    question = historical_question()
    evidence = historical_evidence(question)
    assert market_price_context(question, evidence) is None
    assert len(evidence) == 2
    assert all(item.retrieved_at <= question.as_of for item in evidence)
    model = BacktestModel(can_estimate=True)
    record = RunRecord(run_id="run_historical_estimate", question=question, evidence_mode="import", model="fake")

    state = build_graph(record, evidence, model, Path("/tmp")).invoke({"question": question.model_dump(mode="json")})

    assert state["review"]["probability_basis"] == "evidence_only"
    assert state["forecast"]["status"] == "completed"
    assert state["forecast"]["probabilities"] == {"是": 0.65, "否": 0.35}
    assert state["forecast"]["calibrated"] is False
    for claim in state["forecast"]["supporting"] + state["forecast"]["opposing"]:
        assert set(claim["evidence_ids"]) <= {"E001", "E002"}
        assert claim["assumption_ids"] == []
        assert claim["simulation_ids"] == []
    assert "evidence_audit" in [role for role, _ in model.calls]
    for role in ("evidence_audit", "forecast"):
        payload = next(payload for called_role, payload in model.calls if called_role == role)
        assert "world" not in payload
        assert "actions" not in payload
        assert "simulation" not in payload
        assert [item["id"] for item in payload["evidence"]] == ["E001", "E002"]


def test_no_evidence_still_blocks_probability():
    question = historical_question()
    model = BacktestModel(can_estimate=False)
    record = RunRecord(run_id="run_no_evidence", question=question, evidence_mode="import", model="fake")

    state = build_graph(record, [], model, Path("/tmp")).invoke({"question": question.model_dump(mode="json")})

    assert state["review"]["probability_basis"] == "none"
    assert state["forecast"]["status"] == "insufficient_evidence"
    assert state["forecast"]["probabilities"] is None
    assert "evidence_audit" not in [role for role, _ in model.calls]


def test_evidence_only_probability_rejects_model_assumptions():
    question = historical_question()
    world = WorldState(summary="测试世界", assumptions=[Assumption(id="A1", created_by="model", content="计划不会延期")])
    review = Review(status="blocked", probability_basis="evidence_only")
    forecast = Forecast(status="completed", conclusion="测试", probabilities={"是": .6, "否": .4},
                        supporting=[Claim(text="官方计划按期发布", evidence_ids=["E001"], assumption_ids=["A1"])])
    with pytest.raises(ValueError, match="不能引用假设"):
        validate_forecast(forecast, question, historical_evidence(question), world, [], review)


def test_report_validation_failure_is_not_labeled_missing_evidence():
    question = historical_question()
    forecast = Forecast(status="completed", conclusion="模型报告", probabilities={"是": .6, "否": .4},
                        key_assumptions=["并不存在的假设编号"])
    repaired = repair_forecast(forecast, question, historical_evidence(question), WorldState(summary="测试"),
                               [], Review(status="qualified"), "关键假设引用不存在")
    assert repaired.status == "partial"
    assert repaired.probabilities is None
    assert "内容或引用校验" in repaired.conclusion


def test_dated_reconstructed_market_evidence_can_be_used_with_audit_label():
    example = Path(__file__).resolve().parents[2] / "examples" / "star50-2026-0630-backtest-evidence.json"
    question = QuestionSpec.model_validate({
        "question": "2026年7月31日科创50指数收盘点位是否高于2026年6月30日的2207.86点？",
        "as_of": "2026-06-30T23:59:00+08:00",
        "resolve_by": "2026-07-31T15:30:00+08:00",
        "resolution_rule": "严格高于2207.86点为是，否则为否。",
    })
    items = [ImportedEvidence.model_validate(item) for item in json.loads(example.read_text(encoding="utf-8"))["evidence"]]
    evidence = normalize_import(items, question)
    assert len(evidence) == 3
    assert all(item.retrieved_at > question.as_of for item in evidence)
    assert all(available_at_cutoff(item, question.as_of) for item in evidence)
    assert not available_at_cutoff(evidence[0].model_copy(update={"published_at": None}), question.as_of)

    class MarketModel:
        def complete(self, role, payload, schema, instructions):
            ids = [item["id"] for item in payload.get("evidence", [])]
            if role == "evidence":
                output = {"summary": "短期动量较强，但波动和宏观分化明显。", "evidence_ids": ids}
            elif role == "world":
                output = {"summary": "市场方向未定。", "actors": [], "assumptions": [], "evidence_refs": ids}
            elif role == "review":
                output = {"status": "blocked", "issues": [{"severity": "high", "claim": "推演未覆盖价格回撤", "explanation": "不应据此提高概率。"}]}
            elif role == "evidence_audit":
                output = {"can_estimate": True}
            elif role == "forecast":
                output = {"status": "completed", "conclusion": "仅凭事前资料给出有保留的方向判断。",
                          "probabilities": {"是": 0.55, "否": 0.45},
                          "supporting": [{"text": "近期价格动量较强。", "evidence_ids": ["E001"]}],
                          "opposing": [{"text": "较大日内波动增加回撤可能。", "evidence_ids": ["E002"]}]}
            else:
                raise AssertionError(role)
            return schema.model_validate(output)

    record = RunRecord(run_id="run_market_exercise", question=question, evidence_mode="import", model="fake")
    state = build_graph(record, evidence, MarketModel(), Path("/tmp")).invoke(
        {"question": question.model_dump(mode="json")})
    assert state["review"]["probability_basis"] == "evidence_only"
    assert state["forecast"]["status"] == "completed"
    assert state["forecast"]["probabilities"] == {"是": .55, "否": .45}
    assert any("非截至日冻结快照" in item for item in state["forecast"]["limitations"])


def test_market_evidence_only_forecast_ignores_future_result_gap_and_returns_probability():
    """A missing future close must not contaminate an approved evidence-only estimate."""
    example = Path(__file__).resolve().parents[2] / "examples" / "star50-2026-0630-backtest-evidence.json"
    question = QuestionSpec.model_validate({
        "question": "2026年7月31日科创50指数收盘点位是否高于2026年6月30日的2207.86点？",
        "as_of": "2026-06-30T23:59:00+08:00",
        "resolve_by": "2026-07-31T15:30:00+08:00",
        "resolution_rule": "严格高于2207.86点为是，否则为否。",
    })
    imported = [ImportedEvidence.model_validate(item) for item in json.loads(example.read_text(encoding="utf-8"))["evidence"]]
    evidence = normalize_import(imported, question)

    class FutureGapModel:
        def __init__(self):
            self.forecast_calls = []

        def complete(self, role, payload, schema, instructions):
            ids = [item["id"] for item in payload.get("evidence", [])]
            if role == "evidence":
                output = {
                    "summary": "6月30日收盘为2207.86点，但没有7月行情，因而无法预测。",
                    "evidence_ids": ids,
                    "gaps": ["缺少2026年7月31日的实际收盘数据。"],
                }
            elif role == "world":
                output = {
                    "summary": "缺少7月收盘数据，无法判断后续走势。",
                    "actors": [], "assumptions": [], "evidence_refs": ids,
                }
            elif role == "review":
                output = {
                    "status": "blocked",
                    "issues": [{
                        "severity": "high", "claim": "市场推演缺少可核查的行为路径",
                        "explanation": "不能把未发生的市场行为当作依据。",
                    }],
                    "missing_evidence": ["缺少2026年7月31日的实际收盘数据。"],
                }
            elif role == "evidence_audit":
                output = {"can_estimate": True}
            elif role == "forecast":
                self.forecast_calls.append((payload, instructions))
                # Reproduce the failure: contaminated input or a conflicting null
                # instruction makes the model omit its otherwise available estimate.
                contaminated = "evidence_assessment" in payload or "必须用 null" in instructions
                output = {
                    "status": "completed",
                    "conclusion": "依据截至6月30日的价格和宏观资料，谨慎估计上涨概率。",
                    "probabilities": None if contaminated else {"是": .52, "否": .48},
                    "supporting": [{"text": "近期动量较强。", "evidence_ids": ["E001"]}],
                    "opposing": [{"text": "日内波动和回调风险明显。", "evidence_ids": ["E002"]}],
                    "key_assumptions": ["假设7月没有突发冲击"],
                    "limitations": ["缺少2026年7月31日的实际收盘数据，因此无法预测。"],
                }
            else:
                raise AssertionError(role)
            return schema.model_validate(output)

    model = FutureGapModel()
    record = RunRecord(run_id="run_market_future_gap", question=question, evidence_mode="import", model="fake")
    state = build_graph(record, evidence, model, Path("/tmp")).invoke(
        {"question": question.model_dump(mode="json")})

    assert state["review"]["probability_basis"] == "evidence_only"
    assert "无法预测" not in state["evidence_assessment"]["summary"]
    assert not any("7月31日的实际收盘数据" in gap for gap in state["evidence_assessment"]["gaps"])
    assert not any("7月31日的实际收盘数据" in gap for gap in state["review"]["missing_evidence"])
    assert state["forecast"]["status"] == "completed"
    assert state["forecast"]["probabilities"] == {"是": .52, "否": .48}
    assert state["forecast"]["key_assumptions"] == []
    assert not any("无法预测" in item for item in state["forecast"]["limitations"])
    assert len(model.forecast_calls) == 1
    payload, instructions = model.forecast_calls[0]
    assert "evidence_assessment" not in payload
    assert "world" not in payload
    assert "review" not in payload
    assert "必须用 null" not in instructions
    assert payload["market_price_context"]["dated_price_observation_days"] == 2
    assert payload["market_price_context"]["price_source_groups"] == 1
    assert "没有查到利空消息不是上涨证据" in instructions
    assert any("价格资料只有 2 个交易日" in item for item in state["forecast"]["limitations"])
