"""CPU-only transport tests: no model service or GPU is contacted."""
import asyncio
import threading
import time

import httpx
import pytest
from app import config
from app.llm import BudgetExceeded, ModelCancelled, ModelClient, ModelRequestTimeout
from app.schemas import QuestionAnalysis

_REAL_ASYNC_HTTP = httpx.AsyncHTTPTransport.handle_async_request


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, 'MODEL_API_KEY', 'test-only')
    monkeypatch.setattr(config, 'MODEL_BASE_URL', 'https://model.example.test/v1')
    monkeypatch.setattr(config, 'MAX_SECONDS', 10)
    monkeypatch.setenv('FORECASTLAB_MODEL_TIMEOUT', '2')


def test_cancel_before_request_spends_no_call(configured):
    event = threading.Event(); event.set()
    model = ModelClient(cancel_event=event)
    with pytest.raises(ModelCancelled):
        model.complete('question', {}, QuestionAnalysis, '')
    assert model.usage['calls'] == 0


def test_cancel_during_http_aborts_transport_and_ledger(configured, monkeypatch):
    event, entered, closed = threading.Event(), threading.Event(), threading.Event()
    async def delayed(self, request):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            closed.set()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', delayed)
    model = ModelClient(cancel_event=event)
    timer = threading.Thread(target=lambda: (entered.wait(1), event.set()))
    timer.start()
    started = time.monotonic()
    with pytest.raises(ModelCancelled):
        model.complete('question', {}, QuestionAnalysis, '')
    timer.join()
    assert time.monotonic() - started < 1
    assert closed.is_set()
    assert model.usage['calls'] == 1
    assert model.call_records[0].status == 'interrupted'
    assert model.call_records[0].error_type == 'ModelCancelled'


def test_retry_backoff_shares_one_deadline(configured, monkeypatch):
    monkeypatch.setenv('FORECASTLAB_MODEL_TIMEOUT', '.12')
    requests = []
    async def disconnected(self, request):
        requests.append(request)
        raise httpx.ConnectError('offline', request=request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', disconnected)
    model = ModelClient()
    started = time.monotonic()
    with pytest.raises(ModelRequestTimeout):
        model.complete('question', {}, QuestionAnalysis, '')
    assert len(requests) == 1
    assert model.usage['calls'] == 1
    assert time.monotonic() - started < .5


def test_total_deadline_survives_a_dripping_response(configured, monkeypatch):
    monkeypatch.setenv('FORECASTLAB_MODEL_TIMEOUT', '.12')
    closed = threading.Event()
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b' '
        async def aclose(self):
            closed.set()
    async def drip(self, request):
        return httpx.Response(200, request=request, stream=Drip())
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', drip)
    model = ModelClient()
    started = time.monotonic()
    with pytest.raises(ModelRequestTimeout):
        model.complete('question', {}, QuestionAnalysis, '')
    assert time.monotonic() - started < .5
    assert closed.is_set()
    assert model.usage['calls'] == 1


def test_remaining_run_budget_bounds_inflight_request(configured, monkeypatch):
    closed = threading.Event()
    async def delayed(self, request):
        try:
            await asyncio.sleep(30)
        finally:
            closed.set()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', delayed)
    model = ModelClient(initial_active_seconds=9.85)
    with pytest.raises(BudgetExceeded):
        model.complete('question', {}, QuestionAnalysis, '')
    assert closed.is_set()
    assert model.call_records[0].error_type == 'BudgetExceeded'


def test_tokenizer_is_also_cancelled_before_reservation(configured, monkeypatch):
    monkeypatch.setattr(config, 'MODEL_BASE_URL', 'http://127.0.0.1:18048/v1')
    event = threading.Event()
    async def delayed(self, request):
        assert request.url.path == '/tokenize'
        event.set()
        await asyncio.sleep(30)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', delayed)
    model = ModelClient(cancel_event=event)
    with pytest.raises(ModelCancelled):
        model.complete('question', {}, QuestionAnalysis, '')
    assert model.usage['calls'] == 0


def test_success_response_and_usage_preserved(configured, monkeypatch):
    async def ready(self, request):
        return httpx.Response(200, request=request, json={
            'id': 'fixture', 'object': 'chat.completion', 'created': 1, 'model': 'fixture',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant',
                'content': '{"normalized_question":"test","search_queries":["source"]}'}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 6, 'total_tokens': 18}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', ready)
    model = ModelClient()
    result = model.complete('question', {}, QuestionAnalysis, '')
    assert result.search_queries == ['source']
    assert model.usage == {'calls': 1, 'prompt_tokens': 12, 'completion_tokens': 6}


def test_real_loopback_cancellation_closes_socket(configured, monkeypatch):
    """A stub socket, not a model: verify cancellation reaches the remote peer."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import select
    import socket
    entered, disconnected, cancel = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            entered.set()
            end = time.monotonic()+2
            while time.monotonic() < end:
                if select.select([self.connection], [], [], .05)[0]:
                    if self.connection.recv(1, socket.MSG_PEEK) == b'':
                        disconnected.set()
                        return
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def local_only(transport, request):
        assert request.url.host == '127.0.0.1' and request.url.port == server.server_port
        return await _REAL_ASYNC_HTTP(transport, request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', local_only)
    monkeypatch.setattr(config, 'MODEL_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
    model = ModelClient(cancel_event=cancel)
    model.context_counter = None  # Stub receives only the generation-shaped HTTP request.
    canceller = threading.Thread(target=lambda: (entered.wait(1), cancel.set()))
    canceller.start()
    try:
        with pytest.raises(ModelCancelled):
            model.complete('question', {}, QuestionAnalysis, '')
        assert disconnected.wait(1), 'Cancelled HTTP left the remote socket open'
        assert model.call_records[0].error_type == 'ModelCancelled'
    finally:
        canceller.join(timeout=2)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
