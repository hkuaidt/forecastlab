import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "eval" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_entailment_judge_prompt_is_quote_only():
    m = load("evidence_entailment_judge.py")
    prompt = m.JUDGE_PROMPT
    assert "Use only the quote text" in prompt
    assert "publisher" in prompt
    assert "outside knowledge" in prompt
    assert "unresolved pronouns" in prompt


def test_entailment_judge_requires_explicit_primary_fallback(monkeypatch):
    m = load("evidence_entailment_judge.py")
    monkeypatch.delenv("ENTAILMENT_JUDGE_API_KEY", raising=False)
    monkeypatch.setattr(m.config, "MODEL_API_KEY", "primary-key")
    try:
        m._judge_config(False)
    except SystemExit as exc:
        assert "--allow-primary-model" in str(exc)
    else:
        raise AssertionError("same-model fallback must be explicit")


def test_entailment_judge_prefers_dedicated_model(monkeypatch):
    m = load("evidence_entailment_judge.py")
    monkeypatch.setenv("ENTAILMENT_JUDGE_API_KEY", "judge-key")
    monkeypatch.setenv("ENTAILMENT_JUDGE_BASE_URL", "https://judge.invalid/v1")
    monkeypatch.setenv("ENTAILMENT_JUDGE_MODEL", "judge-model")
    key, base, model, same = m._judge_config(False)
    assert (key, base, model, same) == (
        "judge-key", "https://judge.invalid/v1", "judge-model", False
    )


def test_calibration_scores_strict_gate_and_thresholds():
    m = load("score_entailment_judge.py")
    human = {"rows": [
        {"case_id": "A", "finding_id": "F1", "human_label": "supported"},
        {"case_id": "A", "finding_id": "F2", "human_label": "partially_supported"},
        {"case_id": "B", "finding_id": "F1", "human_label": "supported"},
        {"case_id": "B", "finding_id": "F2", "human_label": "unsupported"},
    ]}
    judged = {"rows": [
        {"case_id": "A", "finding_id": "F1", "judge": {"label": "entailed", "confidence": 0.98}},
        {"case_id": "A", "finding_id": "F2", "judge": {"label": "partially_entailed", "confidence": 0.94}},
        {"case_id": "B", "finding_id": "F1", "judge": {"label": "entailed", "confidence": 0.70}},
        {"case_id": "B", "finding_id": "F2", "judge": {"label": "entailed", "confidence": 0.55}},
    ]}
    result = m.score(human, judged)
    gate = result["binary_strict_gate"]
    assert gate["tp_supported_accept"] == 2
    assert gate["tn_nonstrict_block"] == 1
    assert gate["fp_nonstrict_accept"] == 1
    assert gate["fn_supported_block"] == 0
    at_09 = next(x for x in result["thresholds"] if x["threshold"] == 0.9)
    assert at_09["accepted"] == 1
    assert at_09["strict_support_among_accepted"] == 1.0
    assert at_09["non_strict_leaks"] == []


def test_nli_label_normalization_without_optional_runtime():
    m = load("evidence_nli_judge.py")
    assert m._label_kind("ENTAILMENT") == "entailment"
    assert m._label_kind("contradiction") == "contradiction"
    assert m._label_kind("LABEL_NEUTRAL") == "neutral"


def test_hard_negative_benchmark_is_large_balanced_and_reproducible():
    m = load("build_entailment_benchmark.py")
    data = m.build()
    assert data["row_count"] == 317
    assert data["label_counts"]["entailed"] == 104
    assert data["label_counts"]["partially_entailed"] == 107
    assert data["label_counts"]["not_entailed"] == 105
    assert data["label_counts"]["unclear"] == 1
    assert len({row["id"] for row in data["rows"]}) == data["row_count"]

    by_id = {row["id"]: row for row in data["rows"]}
    hard = [row for row in data["rows"] if row["origin"] == "controlled_hard_negative"]
    assert len(hard) == 182
    for row in hard:
        base = by_id[row["mutation"]["base_id"]]
        assert row["quotes"] == base["quotes"]
        assert row["claim"] != base["claim"]


def test_benchmark_has_multiple_hard_negative_phenomena():
    m = load("build_entailment_benchmark.py")
    data = m.build()
    phenomena = data["phenomenon_counts"]
    assert phenomena["numeric_shift"] >= 90
    assert phenomena["quote_external_source_attribution"] >= 70
    for name in ("event_negation", "quantifier_flip", "entity_status_flip", "ranking_flip", "stance_flip", "modality_flip"):
        assert phenomena[name] >= 1


def test_benchmark_scorer_reports_precision_recall_and_wilson():
    m = load("score_entailment_benchmark.py")
    benchmark = {"rows": [
        {"id": "1", "gold_label": "entailed", "phenomenon": "natural", "case_id": "A", "finding_id": "F1"},
        {"id": "2", "gold_label": "partially_entailed", "phenomenon": "natural", "case_id": "A", "finding_id": "F2"},
        {"id": "3", "gold_label": "not_entailed", "phenomenon": "numeric_shift", "case_id": "B", "finding_id": "F1"},
    ]}
    judged = {"judge_model": "j", "independent_model": True, "rows": [
        {"id": "1", "judge": {"label": "entailed", "confidence": 0.99}},
        {"id": "2", "judge": {"label": "entailed", "confidence": 0.91}},
        {"id": "3", "judge": {"label": "not_entailed", "confidence": 0.95}},
    ]}
    result = m.score(benchmark, judged, thresholds=(0.9,))
    row = result["thresholds"][0]
    assert row["accepted"] == 2
    assert row["strict_support_among_accepted"] == 0.5
    assert row["supported_recall"] == 1.0
    assert row["false_accept_count"] == 1
    assert row["strict_support_wilson95"][0] < 0.5 < row["strict_support_wilson95"][1]


def test_cascade_agreement_requires_both_judges():
    m = load("cascade_entailment_judges.py")
    benchmark = {"rows": [
        {"id": "1", "gold_label": "entailed", "claim": "a"},
        {"id": "2", "gold_label": "partially_entailed", "claim": "b"},
    ]}
    nli = {"judge_model": "nli", "independent_model": True, "rows": [
        {"id": "1", "judge": {"label": "entailed", "confidence": 0.96, "entailment_probability": 0.96, "contradiction_probability": 0.01}},
        {"id": "2", "judge": {"label": "entailed", "confidence": 0.97, "entailment_probability": 0.97, "contradiction_probability": 0.01}},
    ]}
    llm = {"judge_model": "llm", "independent_model": True, "same_as_generator_model": False, "rows": [
        {"id": "1", "judge": {"label": "entailed", "confidence": 0.97}},
        {"id": "2", "judge": {"label": "partially_entailed", "confidence": 0.95}},
    ]}
    result = m.combine(benchmark, nli, llm, nli_accept=0.9, nli_reject=0.9, llm_accept=0.9, policy="agreement")
    assert result["rows"][0]["judge"]["label"] == "entailed"
    assert result["rows"][1]["judge"]["label"] == "partially_entailed"


def test_post_boundary_benchmark_targets_semantic_errors():
    m = load("build_entailment_benchmark.py")
    data = m.build_post_boundary()
    assert data["row_count"] == 239
    assert data["label_counts"] == {
        "entailed": 104,
        "partially_entailed": 91,
        "unclear": 1,
        "not_entailed": 43,
    }
    phenomena = data["phenomenon_counts"]
    assert phenomena["plan_to_actual"] >= 30
    assert phenomena["causal_strengthening"] >= 20
    assert phenomena["scope_expansion"] >= 20
    assert phenomena["exclusivity_strengthening"] >= 15
    assert all(row["phenomenon"] != "numeric_shift" for row in data["rows"])
    assert all(row["phenomenon"] != "quote_external_source_attribution" for row in data["rows"])


def test_local_llm_judge_json_extraction():
    m = load("evidence_local_llm_judge.py")
    parsed = m.extract_json('{"label":"partially_entailed","confidence":0.8,"unsupported_spans":["x"],"rationale":"r"}')
    assert parsed["label"] == "partially_entailed"
    assert parsed["confidence"] == 0.8


def test_benchmark_split_is_case_grouped_without_mutation_leakage():
    m = load("build_entailment_benchmark.py")
    for data, expected in [(m.build(), {"dev": 208, "test": 109}), (m.build_post_boundary(), {"dev": 157, "test": 82})]:
        counts = {"dev": 0, "test": 0}
        by_id = {row["id"]: row for row in data["rows"]}
        for row in data["rows"]:
            counts[row["split"]] += 1
            if row.get("mutation"):
                assert row["split"] == by_id[row["mutation"]["base_id"]]["split"]
        assert counts == expected


def test_benchmark_scorer_can_hold_out_test_split():
    m = load("score_entailment_benchmark.py")
    benchmark = {"rows": [
        {"id": "d", "split": "dev", "gold_label": "entailed", "phenomenon": "natural", "case_id": "A", "finding_id": "F1"},
        {"id": "t1", "split": "test", "gold_label": "entailed", "phenomenon": "natural", "case_id": "B", "finding_id": "F1"},
        {"id": "t2", "split": "test", "gold_label": "not_entailed", "phenomenon": "plan_to_actual", "case_id": "B", "finding_id": "F2"},
    ]}
    judged = {"rows": [
        {"id": "d", "judge": {"label": "entailed", "confidence": 0.99}},
        {"id": "t1", "judge": {"label": "entailed", "confidence": 0.99}},
        {"id": "t2", "judge": {"label": "not_entailed", "confidence": 0.99}},
    ]}
    result = m.score(benchmark, judged, thresholds=(0.9,), split="test")
    assert result["benchmark_rows"] == 2
    assert result["judged_rows"] == 2
    assert result["split"] == "test"
    assert result["four_class_exact_accuracy"] == 1.0


def test_nli_then_llm_cascade_respects_llm_accept_threshold():
    m = load("cascade_entailment_judges.py")
    benchmark = {"rows": [{"id": "x", "gold_label": "partially_entailed", "claim": "x"}]}
    nli = {"judge_model": "nli", "independent_model": True, "rows": [
        {"id": "x", "judge": {"label": "unclear", "confidence": 0.5, "entailment_probability": 0.5, "contradiction_probability": 0.1}},
    ]}
    llm = {"judge_model": "llm", "independent_model": True, "same_as_generator_model": False, "rows": [
        {"id": "x", "judge": {"label": "entailed", "confidence": 0.7}},
    ]}
    result = m.combine(benchmark, nli, llm, nli_accept=0.9, nli_reject=0.9, llm_accept=0.9, policy="nli_then_llm")
    assert result["rows"][0]["judge"]["label"] == "unclear"
    assert result["rows"][0]["judge"]["route"] == "llm_low_confidence"
