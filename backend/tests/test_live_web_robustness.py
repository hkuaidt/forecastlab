from datetime import datetime, timedelta, timezone

from app.agents.evidence import _future_information_gap, _source_gaps
from app.graph import available_at_cutoff, mistakes_future_outcome_for_missing_evidence, sanitize_review_issue_ids
from app.schemas import Evidence, GapDetail, QuestionSpec, RetrievalResult, ReviewIssue, utcnow


def evidence(eid: str, *, published_at=None, availability="unverified"):
    now = utcnow()
    return Evidence(
        id=eid,
        source_url=f"https://example.org/{eid.lower()}",
        title=f"Source {eid}",
        publisher="example.org",
        published_at=published_at,
        retrieved_at=now,
        excerpt="body text",
        content_hash="a" * 64,
        source_type="secondary",
        source_group=f"group-{eid}",
        content_kind="body",
        availability=availability,
    )


def binary_question():
    now = utcnow()
    return QuestionSpec(
        question="阿森纳是否会在本赛季最终排名前四？",
        as_of=now,
        resolve_by=now + timedelta(days=200),
        resolution_rule="最终积分榜前四为是，否则为否。",
    )


def test_live_near_cutoff_is_usable_but_unverified_is_not():
    q = binary_question()
    live = evidence("E001", availability="live_near_cutoff").model_copy(
        update={"retrieved_at": q.as_of + timedelta(seconds=30)}
    )
    plain = live.model_copy(update={"availability": "unverified"})
    assert available_at_cutoff(live, q.as_of) is True
    assert available_at_cutoff(plain, q.as_of) is False


def test_future_final_table_is_not_an_evidence_gap():
    q = QuestionSpec(
        question="阿森纳是否会在本赛季最终排名前四？",
        as_of=datetime(2026, 9, 30, tzinfo=timezone.utc),
        resolve_by=datetime(2027, 6, 30, tzinfo=timezone.utc),
        resolution_rule="最终积分榜前四为是，否则为否。",
    )
    gap = GapDetail(missing="英超官网发布的 2026/27 赛季最终积分榜原文。")
    assert _future_information_gap(gap, q) is True
    assert mistakes_future_outcome_for_missing_evidence("缺少赛季最终积分榜", q, assume_missing=True) is True


def test_unknown_publication_dates_are_aggregated():
    items = [evidence(f"E{i:03}") for i in range(1, 11)]
    gaps = _source_gaps(RetrievalResult(evidence=items))
    date_gaps = [gap for gap in gaps if gap.cause == "date_unknown"]
    assert len(date_gaps) == 1
    assert "10 条来源" in date_gaps[0].missing
    assert "E001" in date_gaps[0].missing


def test_invalid_review_locator_is_dropped_without_trusting_it():
    issue = ReviewIssue(
        severity="medium",
        claim="检索任务没有覆盖该信息",
        explanation="需要补充核查。",
        affected_ids=["E001", "R002", "F001（相关发现）"],
    )
    invalid = sanitize_review_issue_ids(issue, {"E001", "F001"})
    assert issue.affected_ids == ["E001", "F001"]
    assert invalid == ["R002"]
    assert "系统已忽略无效定位编号：R002" in issue.explanation


def test_world_ids_are_server_canonicalized():
    from app.graph import canonicalize_world_ids
    from app.schemas import ActorProfile, Assumption, WorldState

    world = WorldState(
        summary="test",
        actors=[
            ActorProfile(id="team", name="Team", goal="ship"),
            ActorProfile(id="maintainer", name="Maintainer", goal="stability"),
        ],
        assumptions=[
            Assumption(id="P1", created_by="model", content="first", parent_ids=["F001"]),
            Assumption(id="AS1", created_by="model", content="second", parent_ids=["P1", "E001"]),
        ],
    )
    actor_map, assumption_map = canonicalize_world_ids(world)
    assert actor_map == {"team": "A001", "maintainer": "A002"}
    assert assumption_map == {"P1": "H001", "AS1": "H002"}
    assert [a.id for a in world.actors] == ["A001", "A002"]
    assert [a.id for a in world.assumptions] == ["H001", "H002"]
    assert world.assumptions[1].parent_ids == ["H001", "E001"]


def test_world_id_canonicalization_rejects_duplicates():
    import pytest
    from app.graph import canonicalize_world_ids
    from app.schemas import ActorProfile, WorldState

    world = WorldState(summary="test", actors=[
        ActorProfile(id="dup", name="A", goal="x"),
        ActorProfile(id="dup", name="B", goal="y"),
    ])
    with pytest.raises(ValueError, match="主体 ID 重复"):
        canonicalize_world_ids(world)
