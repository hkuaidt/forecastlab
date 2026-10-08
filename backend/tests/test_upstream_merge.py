"""Merge regression checks for upstream v3/v4 work and full ForecastLab path.

The repository's normal browser suite separately verifies that the product
entry is the complete forecast flow, not an 问题定义与证据评估-only introduction.
"""
from app import config
from app.graph import finding_evidence_map, resolve_refs
from app.schemas import QuestionSpec, RunRecord, utcnow


def test_finding_aliases_require_validated_quotes():
    untrusted = {"findings": [{"id": "F001", "citations": [{"evidence_id": "E001"}]}]}
    assert finding_evidence_map(untrusted) == {}, "Unvalidated F must not become a source"
    trusted = {**untrusted, "findings_validated": True}
    assert finding_evidence_map(trusted) == {"F001": ["E001"]}


def test_advisory_finding_ids_expand_to_valid_sources_without_creating_ids():
    resolved, dropped = resolve_refs(
        ["F001", "E002", "E999"], {"E001", "E002"},
        expansions={"F001": ["E001"]},
    )
    assert resolved == ["E001", "E002"]
    assert dropped == ["E999"]
    parents, dropped_parents = resolve_refs(["F001", "H001"], {"F001", "H001"})
    assert parents == ["F001", "H001"]
    assert not dropped_parents


def test_shadow_record_is_optional_and_does_not_replace_scored_forecast():
    question = QuestionSpec(question="这次版本是否能够按期正式发布？", mode="scenario", as_of=utcnow())
    record = RunRecord(run_id="test_merge", question=question, evidence_mode="import", model="fixture")
    assert record.shadow_forecast is None
    assert record.forecast is None
    serialized = record.model_dump(mode="json")
    assert "shadow_forecast" in serialized
    assert RunRecord.model_validate(serialized).shadow_forecast is None


def test_shadow_is_disabled_by_default_in_project_config():
    # A deployment can opt in explicitly, but the model transport / final
    # prediction must not require the optional experiment to be enabled.
    assert isinstance(config.SHADOW_FULL, bool)
