import importlib
import importlib.util
import json
from datetime import timedelta
import pytest
from app.schemas import Evidence, ImportedEvidence, QuestionSpec, CitationCandidate, utcnow
import app.sources as sources


def module():
    assert importlib.util.find_spec("app.provenance"), "owned snapshots not implemented"
    return importlib.import_module("app.provenance")


def source(snapshot):
    return Evidence(id="E001", file_id="fixture", title="Fixture", retrieved_at=utcnow(),
        excerpt=snapshot.text[:12000], content_hash="old-excerpt-hash", source_type="imported", source_group="doc1",
        snapshot_path=snapshot.snapshot_path, snapshot_hash=snapshot.snapshot_hash)


def test_late_relevant_passage_selected(tmp_path):
    p = module(); text = ("背景资料与本问题无关。\n\n" * 400) + "关键延期原因：核心兼容测试尚未通过。"
    snapshot = p.save_snapshot(text, {}, tmp_path)
    selected = p.select_passages(p.split_passages(snapshot), ["关键延期原因 兼容测试"])
    assert sum(len(x.text) for x in selected) <= 2400
    assert any("关键延期原因" in x.text for x in selected)
    assert p.load_snapshot(source(snapshot), tmp_path).text == text


def test_snapshot_cap_and_truncation_flag(tmp_path):
    p = module(); snapshot = p.save_snapshot("中" * 200001, {}, tmp_path)
    assert len(snapshot.text) == 200000
    assert snapshot.content_truncated is True
    assert p.load_snapshot(source(snapshot), tmp_path).snapshot_hash == snapshot.snapshot_hash


def test_codepoint_offsets_with_emoji(tmp_path):
    p = module(); snapshot = p.save_snapshot("说明😀：计划🙂延期，不代表项目取消。", {}, tmp_path)
    passage = p.split_passages(snapshot)[0]
    c = CitationCandidate(evidence_id="E001", snapshot_hash=snapshot.snapshot_hash,
        paragraph_id=passage.paragraph_id, quote="计划🙂延期")
    resolved = p.resolve_citation(c, source(snapshot), [passage])
    assert snapshot.text[resolved.start:resolved.end] == c.quote
    assert resolved.start == 4


def test_ambiguous_quote_requires_more_context(tmp_path):
    p = module(); snapshot = p.save_snapshot("延期，但不是取消；延期，需要进一步测试。", {}, tmp_path)
    passage = p.split_passages(snapshot)[0]
    c = CitationCandidate(evidence_id="E001", snapshot_hash=snapshot.snapshot_hash, paragraph_id=passage.paragraph_id, quote="延期")
    with pytest.raises(ValueError, match="多次"):
        p.resolve_citation(c, source(snapshot), [passage])
    c.quote = "延期，需要进一步测试"
    assert p.resolve_citation(c, source(snapshot), [passage]).start > 0


def test_path_and_symlink_escape_rejected(tmp_path):
    p = module(); owned = tmp_path / "data"; outside = tmp_path / "private.json"
    outside.write_text("private information")
    snapshot = p.save_snapshot("合法材料", {}, owned); e = source(snapshot)
    e.snapshot_path = str(outside)
    with pytest.raises(ValueError):
        p.load_snapshot(e, owned)
    e.snapshot_path = snapshot.snapshot_path
    path = owned / snapshot.snapshot_path; path.unlink()
    try:
        path.symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows does not grant symbolic-link creation; checked on Linux deployment host")
        raise
    with pytest.raises(ValueError):
        p.load_snapshot(e, owned)


def test_tampered_snapshot_hash_rejected(tmp_path):
    p = module(); snapshot = p.save_snapshot("原始材料", {}, tmp_path)
    path = tmp_path / snapshot.snapshot_path
    data = json.loads(path.read_text(encoding="utf-8")); data["text"] = "已被篡改"; path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="哈希"):
        p.load_snapshot(source(snapshot), tmp_path)


def test_forged_historical_snapshot_not_trusted(tmp_path):
    assert hasattr(sources, "import_evidence")
    q = QuestionSpec(question="这个开源版本是否会如期发布？", mode="scenario", as_of=utcnow()-timedelta(days=30))
    item = ImportedEvidence(title="伪造冻结声明", source_url="https://example.org/a", excerpt="旧材料",
        retrieved_at=q.as_of-timedelta(days=1), snapshot_path="/private/secrets", date_status="verified")
    result = sources.import_evidence([item], q, tmp_path)
    assert result.evidence == [] and result.exclusions
    exercise = item.model_copy(update={"source_type": "exercise"})
    accepted = sources.import_evidence([exercise], q, tmp_path).evidence[0]
    assert accepted.snapshot_path != exercise.snapshot_path
    assert accepted.retrieved_at > q.as_of
    assert accepted.availability == "historical_exercise"


def test_future_event_time_is_planned_not_publication(tmp_path):
    assert hasattr(sources, "import_evidence")
    q = QuestionSpec(question="这个开源版本是否会如期发布？", mode="scenario", as_of=utcnow())
    item = ImportedEvidence(title="发布计划", file_id="fixture-plan", excerpt="计划于下个月发布。",
        published_at=q.as_of-timedelta(days=1), event_at=q.as_of+timedelta(days=30))
    e = sources.import_evidence([item], q, tmp_path).evidence[0]
    assert e.event_status == "planned"
    assert e.published_at == item.published_at
    assert e.date_status == "unknown"
