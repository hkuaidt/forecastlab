"""Selected Brave pages are bounded public reads, never unlabelled snippets."""
import asyncio
import gzip
import hashlib
import json
import socket
import time

import httpx
import pytest

from app import source_fetch as F, sources as S
from app.schemas import QuestionSpec, RetrievalTask, utcnow
from app.provenance import load_snapshot, split_passages


PROSE = "The public research team published a detailed evaluation with methods, limitations, and reproducible results. " * 4


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data, delay=0):
        self.data, self.delay, self.closed = data, delay, False
    async def __aiter__(self):
        for chunk in self.data:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
    async def aclose(self):
        self.closed = True


def response(body, *, status=200, headers=None):
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8", **(headers or {})},
                          stream=Chunks([body.encode() if isinstance(body, str) else body]))


@pytest.fixture
def install(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(F.socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    def configured(handler):
        def factory(**kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(F.httpx, "AsyncClient", factory)
    return configured


def fetch(url="https://public.example/article"):
    return asyncio.run(F.fetch_selected_bodies([url]))[0]


def test_main_body_preserves_prose_omits_navigation_and_pins_ip_tls(install):
    calls = []
    def handler(request):
        calls.append(request)
        return response(f"<head><title>Research report</title></head><nav>Menu invented fact</nav>"
                        f"<main><h1>Official report</h1><p>{PROSE}<strong>Important</strong> conclusion.</p>"
                        "<script>Fake fact</script><p hidden>Hidden claim</p></main><footer>Footer</footer>")
    install(handler)
    body, metadata = fetch()
    assert PROSE in body and "Important conclusion." in body
    assert all(x not in body for x in ["Menu invented fact", "Fake fact", "Hidden claim", "Footer"])
    assert metadata["status"] == "success" and metadata["extraction"] == "article_or_main"
    assert str(calls[0].url) == "https://93.184.216.34/article"
    assert calls[0].headers["host"] == "public.example"
    assert calls[0].extensions["sni_hostname"] == "public.example"


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://10.0.0.1/", "http://224.0.0.1/", "file:///etc/passwd",
    "https://public.example:18765/", "https://user:pass@public.example/", "http://localhost/", "http://public.example\\@127.0.0.1/"])
def test_rejects_unsafe_destinations_before_request(install, url):
    install(lambda request: pytest.fail("Unsafe destination was contacted"))
    body, metadata = fetch(url)
    assert body is None and metadata["reason"] in {"non_public_url", "non_public_address"}


def test_mixed_dns_answer_is_rejected(monkeypatch, install):
    install(lambda request: pytest.fail("Mixed DNS answer was contacted"))
    monkeypatch.setattr(F.socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ["93.184.216.34", "10.2.3.4"]])
    assert fetch()[1]["reason"] == "non_public_address"


def test_redirect_to_private_is_revalidated(install):
    calls = []
    def handler(request):
        calls.append(request)
        return response("", status=302, headers={"location": "http://127.0.0.1/private"})
    install(handler)
    assert fetch("http://public.example/start")[0] is None
    assert len(calls) == 1


def test_relative_redirect_keeps_final_url_and_provenance(install):
    def handler(request):
        if request.url.path == "/start":
            return response("", status=302, headers={"location": "/article"})
        return response(f"<article><p>{PROSE}</p></article>")
    install(handler)
    body, metadata = fetch("https://public.example/start")
    assert body == PROSE.strip()
    assert metadata["final_url"] == "https://public.example/article"
    assert metadata["redirects"] == [{"url": "https://public.example/start", "status": 302}]


def test_redirect_loop_and_https_downgrade_are_bounded(install):
    calls = []
    def handler(request):
        calls.append(request)
        return response("", status=302, headers={"location": "/again"})
    install(handler)
    assert fetch()[1]["reason"] == "redirect_limit"
    assert len(calls) == F.MAX_REDIRECTS + 1
    install(lambda request: response("", status=302, headers={"location": "http://public.example/article"}))
    assert fetch()[1]["reason"] == "https_downgrade"


@pytest.mark.parametrize("html", [
    "<title>Just a moment...</title><p>Verify you are human.</p>",
    f"<article><p>{PROSE}</p></article><div class='paywall'>Subscribe to read</div>",
    "<main>Sign in to continue. " + "Login or subscribe. " * 25 + "</main>",
    "<main>Accept cookies to continue. " + "Cookie preferences. " * 25 + "</main>",
    "<nav>Links only " * 100 + "</nav>",
    "<main>" + "<a href='/'>Long navigation link without article evidence. </a>" * 20 + "</main>",
])
def test_access_gates_and_navigation_do_not_become_body(install, html):
    install(lambda request: response(html))
    body, metadata = fetch()
    assert body is None and metadata["status"] == "unavailable"


def test_article_about_login_or_copyright_is_not_a_gate(install):
    article = "The study explains why users sign in to public services and why copyright notices matter. " * 8
    install(lambda request: response(f"<title>Studying login and copyright</title><article><p>{article}</p></article>"))
    assert fetch()[0] == article.strip()


@pytest.mark.parametrize("mode", ["length", "stream", "gzip"])
def test_size_limits_include_decompressed_body(install, monkeypatch, mode):
    monkeypatch.setattr(F, "MAX_RESPONSE_BYTES", 512)
    content = b"x" * 1024
    headers = {}
    if mode == "length": headers["content-length"] = "999999"
    if mode == "gzip": content, headers = gzip.compress(content), {"content-encoding": "gzip"}
    install(lambda request: response(content, headers=headers))
    assert fetch()[1]["reason"] == "response_too_large"


def test_plain_text_and_unsupported_pdf(install):
    install(lambda request: response(PROSE, headers={"content-type": "text/plain; charset=utf-8"}))
    assert fetch()[1]["extraction"] == "plain_text"
    install(lambda request: response(b"%PDF-body", headers={"content-type": "application/pdf"}))
    assert fetch()[1]["reason"] == "unsupported_content_type"


def test_slow_drip_total_timeout_closes_response(monkeypatch, install):
    monkeypatch.setattr(F, "PAGE_TIMEOUT_SECONDS", .06)
    stream = Chunks([b"x"] * 100, delay=.02)
    install(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, stream=stream))
    start = time.monotonic()
    assert fetch()[1]["reason"] == "timeout"
    assert time.monotonic() - start < .5 and stream.closed


def test_dns_stall_does_not_block_batch_shutdown(monkeypatch):
    monkeypatch.setattr(F, "PAGE_TIMEOUT_SECONDS", .03)
    def slow(*a, **kw):
        time.sleep(.2)
        return []
    monkeypatch.setattr(F.socket, "getaddrinfo", slow)
    start = time.monotonic()
    assert fetch()[1]["reason"] == "timeout"
    assert time.monotonic() - start < .15


def test_batch_deadline_and_concurrency_limit(monkeypatch):
    active = peak = 0
    async def slow(url):
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        try:
            await asyncio.sleep(10)
        finally:
            active -= 1
    monkeypatch.setattr(F, "_read_body", slow)
    monkeypatch.setattr(F, "BATCH_TIMEOUT_SECONDS", .04)
    results = asyncio.run(F.fetch_selected_bodies([f"https://example.org/{i}" for i in range(10)]))
    assert peak == 3 and active == 0
    assert len(results) == 10 and all(m["reason"] == "batch_timeout" for _, m in results)


def test_only_selected_brave_results_fetched_and_snapshot_hash_offsets_match(monkeypatch, tmp_path):
    monkeypatch.setattr(S.config, "BRAVE_SEARCH_API_KEY", "test-only")
    monkeypatch.setattr(S, "_search_one", lambda q, **kwargs: [
        {"url": f"https://public.example/{q}/{i}", "title": f"Research {q} {i}",
         "content": f"Search excerpt {q} {i}", "score": .9} for i in range(8)])
    fetched = []
    async def bodies(urls):
        fetched.extend(urls)
        return [(PROSE + url, {"status": "success", "final_url": url, "fetched_at": utcnow().isoformat()})
                if i else (None, {"status": "unavailable", "reason": "http_403"}) for i, url in enumerate(urls)]
    monkeypatch.setattr(F, "fetch_selected_bodies", bodies)
    q = QuestionSpec(question="What is the future research outlook?", mode="scenario", as_of=utcnow())
    result = S.retrieve_evidence(q, [RetrievalTask(id=f"R{i:03}", query=str(i), purpose="background") for i in range(1, 4)], tmp_path)
    assert len(fetched) == len(result.evidence) == 10
    snippet, *bodies = result.evidence
    assert snippet.content_kind == "snippet" and snippet.source_type == "snippet_only"
    assert load_snapshot(snippet, tmp_path).metadata["source_metadata"]["body_fetch"]["reason"] == "http_403"
    for item in bodies:
        assert item.content_kind == "body" and item.source_type == "secondary"
        snapshot = load_snapshot(item, tmp_path)
        assert snapshot.text == PROSE + str(item.source_url)
        assert snapshot.snapshot_hash == hashlib.sha256(snapshot.text.encode()).hexdigest()
        assert snapshot.metadata["source_metadata"]["body_fetch"]["status"] == "success"
        assert snapshot.metadata["source_metadata"]["search_excerpt_hash"]
        assert all(p.text == snapshot.text[p.start:p.end] for p in split_passages(snapshot))


def test_tavily_existing_body_does_not_trigger_extra_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(S.config, "BRAVE_SEARCH_API_KEY", "")
    monkeypatch.setattr(S.config, "TAVILY_API_KEY", "test-only")
    monkeypatch.setattr(S, "_search_one", lambda q, **kwargs: [{"url": "https://public.example/article", "raw_content": PROSE, "score": .9}])
    monkeypatch.setattr(F, "fetch_selected_bodies", lambda urls: pytest.fail("Unexpected extra fetch"))
    q = QuestionSpec(question="What is the future research outlook?", mode="scenario", as_of=utcnow())
    assert S.retrieve_evidence(q, [], tmp_path).evidence[0].content_kind == "body"


def test_corrupt_gzip_is_snippet_fallback_without_failing_batch(install):
    install(lambda request: response(b"this is not a gzip stream", headers={"content-encoding": "gzip"}))
    body, metadata = fetch()
    assert body is None
    assert metadata["reason"] == "fetch_failed"


def test_bare_html_attributes_preserve_body_and_hidden_semantics(install):
    install(lambda request: response(
        f"<main id class style aria-hidden><p>{PROSE}</p>"
        "<p hidden>Hidden content must remain excluded.</p></main>"))
    body, metadata = fetch()
    assert body == PROSE.strip()
    assert "Hidden content" not in body
    assert metadata["status"] == "success"
    assert metadata["extraction"] == "article_or_main"
