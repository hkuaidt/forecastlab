"""Brave adapter preserves snippet provenance and does not leak credentials."""
import json
import httpx
import pytest
from app import sources as S
from app.schemas import QuestionSpec, RetrievalTask, utcnow

def test_brave_snippet_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(S.config, "BRAVE_SEARCH_API_KEY", "test-secret")
    monkeypatch.setattr(S.config, "SEARCH_PROXY", "")
    from app import source_fetch
    async def unavailable(urls):
        return [(None, {"status": "unavailable", "reason": "http_403"}) for _ in urls]
    monkeypatch.setattr(source_fetch, "fetch_selected_bodies", unavailable)
    def handle(request):
        assert request.url.host == "api.search.brave.com"
        assert request.headers["X-Subscription-Token"] == "test-secret"
        assert "test-secret" not in str(request.url)
        assert request.url.params["q"] == "release announcement"
        return httpx.Response(200, json={"web": {"results": [{"url": "https://example.org/release",
            "title": "Release announcement", "description": "A <strong>release</strong> &amp; update",
            "extra_snippets": ["Official release announcement excerpt"]}]}})
    real = httpx.Client
    monkeypatch.setattr(S.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    result = S.retrieve_evidence(QuestionSpec(question="release announcement", mode="scenario", as_of=utcnow()),
        [RetrievalTask(id="R001", query="release announcement", purpose="initial")], tmp_path)
    assert result.status == "completed"
    evidence = result.evidence[0]
    assert evidence.content_kind == "snippet" and evidence.source_type == "snippet_only"
    assert evidence.published_at is None and evidence.date_status == "unknown"
    snapshot = json.loads((tmp_path/evidence.snapshot_path).read_text())
    assert snapshot["metadata"]["provider"] == "brave"
    assert "A release & update" in evidence.excerpt
    assert "test-secret" not in result.model_dump_json() + json.dumps(snapshot)

def test_brave_failure_is_redacted(monkeypatch, tmp_path):
    monkeypatch.setattr(S.config, "BRAVE_SEARCH_API_KEY", "test-secret")
    monkeypatch.setattr(S.config, "SEARCH_PROXY", "")
    real = httpx.Client
    monkeypatch.setattr(S.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, json={"error": "test-secret"})), **kw))
    result = S.retrieve_evidence(QuestionSpec(question="release announcement", mode="scenario", as_of=utcnow()),
        [RetrievalTask(id="R001", query="release announcement", purpose="initial")], tmp_path)
    assert result.status == "failed" and result.retrieval_log[0].error == "HTTP 429"
    assert "test-secret" not in result.model_dump_json()
