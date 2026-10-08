from datetime import timedelta
import pytest
import httpx
import app.sources as S
from app.schemas import QuestionSpec, RetrievalTask, utcnow


def question():
    return QuestionSpec(question="青岚项目是否能够按期发布？", mode="scenario", as_of=utcnow())


def tasks():
    return [RetrievalTask(id=f"R{i+1:03}", query=q, purpose=p) for i, (q,p) in enumerate([
        ("release", "initial"), ("risk", "challenge"), ("alternative", "alternative")])]


def install(monkeypatch, fn):
    assert hasattr(S, "retrieve_evidence"), "structured retrieval not implemented"
    monkeypatch.setattr(S.config, "TAVILY_API_KEY", "test-only")
    monkeypatch.setattr(S, "_search_one", fn)


def test_three_query_buckets_survive_ten_source_limit(tmp_path, monkeypatch):
    install(monkeypatch, lambda q: [{"url": f"https://example.org/{q}/{i}", "title": f"{q} {i}",
        "raw_content": f"{q} evidence {i}", "score": .8} for i in range(8)])
    result = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert len(result.evidence) == 10
    assert {q for e in result.evidence for q in e.query_ids} == {"R001", "R002", "R003"}
    repeat = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert [str(e.source_url) for e in repeat.evidence] == [str(e.source_url) for e in result.evidence]


def test_dedup_keeps_all_query_ids_and_alias_dates(tmp_path, monkeypatch):
    def response(q):
        return [{"url": f"https://{q}.example.org/article", "title": q,
                 "raw_content": "完全相同的原始公告。", "score": .9,
                 "published_date": "2026-09-01T00:00:00Z"}]
    install(monkeypatch, response)
    result = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert len(result.evidence) == 1
    e = result.evidence[0]
    assert set(e.query_ids) == {"R001", "R002", "R003"}
    assert len(e.aliases) == 3
    assert all(alias.published_at is not None for alias in e.aliases)


def test_query_parameters_keep_article_identity():
    assert hasattr(S, "canonical_source_url")
    assert S.canonical_source_url("https://example.org/a?id=7&utm_source=x#top") == "https://example.org/a?id=7"
    assert S.canonical_source_url("https://example.org/a?id=8") != S.canonical_source_url("https://example.org/a?id=7")


def test_common_domain_not_equivalent_to_common_origin(tmp_path, monkeypatch):
    install(monkeypatch, lambda q: [{"url": f"https://example.org/{q}", "raw_content": f"不同的公告 {q}", "score": .9}])
    result = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert len({e.source_group for e in result.evidence}) == 3


def test_explicit_reprints_share_root_without_becoming_duplicate_sources(tmp_path, monkeypatch):
    install(monkeypatch, lambda q: [{"url": f"https://{q}.example.org/article", "raw_content":
        f"转载自：https://origin.example.org/announcement\n{q} 的补充说明各不相同。", "score": .9}])
    result = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert len(result.evidence) == 3
    assert len({e.source_group for e in result.evidence}) == 1
    assert all("转载" in e.source_group_basis for e in result.evidence)


def test_partial_and_total_failure_keep_logs(tmp_path, monkeypatch):
    def partially(q):
        if q != "release":
            raise httpx.ConnectError("provider unavailable test-only")
        return [{"url": "https://example.org/a", "content": "release text", "score": .9}]
    install(monkeypatch, partially)
    result = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert result.status == "partial" and len(result.retrieval_log) == 3
    assert "test-only" not in result.model_dump_json()
    monkeypatch.setattr(S, "_search_one", lambda q: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    result = S.retrieve_evidence(question(), tasks(), tmp_path)
    assert result.status == "failed" and all(x.status == "failed" for x in result.retrieval_log)


def test_response_byte_limit(monkeypatch):
    assert hasattr(S, "_search_one")
    consumed = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_bytes(self):
            for _ in range(20):
                consumed.append(1)
                yield b"x" * (1024 * 1024)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(S.httpx, "Client", Client)
    with pytest.raises(ValueError, match="8 MiB"):
        S._search_one("release")
    assert len(consumed) == 9


def test_future_publication_is_excluded_not_redated(tmp_path, monkeypatch):
    q = question()
    install(monkeypatch, lambda query: [{"url": "https://example.org/future", "raw_content": "future news", "score": .9,
        "published_date": (q.as_of+timedelta(days=1)).isoformat()}])
    result = S.retrieve_evidence(q, tasks(), tmp_path)
    assert result.evidence == [] and len(result.exclusions) == 3
