"""CPU-only transport tests: no model, GPU, external URL or production port."""
import asyncio
from contextlib import asynccontextmanager, suppress
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

spec = importlib.util.spec_from_file_location("forecastlab_model_router", Path(__file__).with_name("model_router.py"))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def configuration(tmp_path, ports=(18049, 18050), **extra):
    path = tmp_path / "router.json"
    path.write_text(json.dumps({"upstreams": [f"http://127.0.0.1:{port}/v1" for port in ports],
                               "health_interval_seconds": 60, **extra}))
    return path


@asynccontextmanager
async def client_for(path, handler):
    app = module.create_app(path, transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://router") as client:
            yield client, app.state.router


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


def mock_json(status, data):
    return httpx.Response(status, headers={"content-type": "application/json"},
                          stream=httpx.ByteStream(json.dumps(data).encode()))


class GatedStream(httpx.AsyncByteStream):
    def __init__(self, gate, chunks=(b'{"ok":true}',), failure=None):
        self.gate, self.chunks, self.failure = gate, chunks, failure
        self.closed = False

    async def __aiter__(self):
        await self.gate.wait()
        for chunk in self.chunks:
            yield chunk
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


def test_raw_json_headers_and_sse_extras_are_not_rewritten(tmp_path):
    async def scenario():
        body = b'{"model":"qwen3-8b","messages":[],"extra_body":{"enable_thinking":true},"stream":true}'
        chunks = (b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n',
                  b'data: {"choices":[],"usage":{"completion_tokens":13}}\n\n', b'data: [DONE]\n\n')
        captured = []
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            captured.append((request.url, await request.aread(), dict(request.headers)))
            gate = asyncio.Event(); gate.set()
            return httpx.Response(200, headers={"content-type": "text/event-stream", "x-worker-info": "unchanged"},
                                  stream=GatedStream(gate, chunks))
        async with client_for(configuration(tmp_path), upstream) as (client, router):
            response = await client.post("/v1/chat/completions?trace=1", content=body,
                                         headers={"Authorization": "Bearer private-test-key", "content-type": "application/json"})
            assert response.content == b"".join(chunks)
            assert response.headers["x-worker-info"] == "unchanged"
            assert captured[0][1] == body
            assert captured[0][2]["authorization"] == "Bearer private-test-key"
            assert captured[0][0].query == b"trace=1"
            assert (await router.snapshot())["workers"][0]["active_generations"] == 0
    asyncio.run(scenario())


def test_one_generation_per_worker_bounded_queue_and_tokenize_bypass(tmp_path):
    async def scenario():
        gates = {18049: asyncio.Event(), 18050: asyncio.Event()}
        called = []
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            if request.url.path == "/tokenize": return mock_json(200, {"count": 17, "tokens": [1, 2]})
            called.append(request.url.port)
            return httpx.Response(200, stream=GatedStream(gates[request.url.port]))
        async with client_for(configuration(tmp_path, queue_limit=1), upstream) as (client, router):
            tasks = [asyncio.create_task(client.post("/v1/chat/completions", json={})) for _ in range(2)]
            await until(lambda: len(called) == 2)
            assert set(called) == {18049, 18050}
            queued = asyncio.create_task(client.post("/v1/chat/completions", json={}))
            await until(lambda: len(router.waiters) == 1)
            assert (await client.post("/v1/chat/completions", json={})).status_code == 429
            assert (await client.post("/tokenize", json={"prompt": "text"})).json()["count"] == 17
            health = (await client.get("/health")).json()
            assert [w["active_generations"] for w in health["workers"]] == [1, 1]
            assert health["queued_generations"] == 1
            gates[18049].set()
            await until(lambda: len(called) == 3)
            assert called == [18049, 18050, 18049]
            gates[18050].set()
            assert all(r.status_code == 200 for r in await asyncio.gather(*tasks, queued))
            assert all(w.active == 0 for w in router.workers.values())
    asyncio.run(scenario())


def test_queue_timeout_and_cancelled_waiters_release_capacity(tmp_path):
    async def scenario():
        gate = asyncio.Event()
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            return httpx.Response(200, stream=GatedStream(gate))
        async with client_for(configuration(tmp_path, ports=(18049,), queue_limit=1, queue_timeout_seconds=.1), upstream) as (client, router):
            active = asyncio.create_task(client.post("/v1/chat/completions", json={}))
            await until(lambda: next(iter(router.workers.values())).active == 1)
            assert (await client.post("/v1/chat/completions", json={})).status_code == 504
            assert not router.waiters
            queued = asyncio.create_task(client.post("/v1/chat/completions", json={}))
            await until(lambda: len(router.waiters) == 1)
            queued.cancel()
            with suppress(asyncio.CancelledError): await queued
            assert not router.waiters
            active.cancel()
            with suppress(asyncio.CancelledError): await active
            assert next(iter(router.workers.values())).active == 0
            gate.set()
            assert (await client.post("/v1/chat/completions", json={})).status_code == 200
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["headers", "stream"])
def test_cancelling_active_client_releases_slot_and_response(tmp_path, phase):
    async def scenario():
        gate = asyncio.Event(); stream = GatedStream(gate); entered = asyncio.Event()
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            entered.set()
            if phase == "headers": await gate.wait()
            return httpx.Response(200, stream=stream)
        async with client_for(configuration(tmp_path, ports=(18049,)), upstream) as (client, router):
            task = asyncio.create_task(client.post("/v1/chat/completions", json={}))
            await entered.wait()
            if phase == "stream": await asyncio.sleep(.02)
            task.cancel()
            with suppress(asyncio.CancelledError): await task
            assert next(iter(router.workers.values())).active == 0
            if phase == "stream": assert stream.closed
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,expected_calls,status", [
    (httpx.ConnectError, [18049, 18050], 200),
    (httpx.ConnectTimeout, [18049, 18050], 200),
    (httpx.ReadTimeout, [18049], 502),
    (httpx.WriteError, [18049], 502),
    (httpx.RemoteProtocolError, [18049], 502),
])
def test_only_connection_establishment_failures_are_replayed(tmp_path, failure, expected_calls, status):
    async def scenario():
        calls = []
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            calls.append(request.url.port)
            if request.url.port == 18049: raise failure("test transport failure", request=request)
            return mock_json(200, {"choices": [], "usage": {"total_tokens": 7}})
        async with client_for(configuration(tmp_path), upstream) as (client, router):
            response = await client.post("/v1/chat/completions", json={})
            assert response.status_code == status
            assert calls == expected_calls
            assert all(w.active == 0 for w in router.workers.values())
            if failure in (httpx.ConnectError, httpx.ConnectTimeout):
                assert router.workers["http://127.0.0.1:18049"].healthy is False
    asyncio.run(scenario())


def test_http_errors_are_forwarded_without_retry(tmp_path):
    async def scenario():
        calls = []
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            calls.append(request.url.port)
            return httpx.Response(400, stream=httpx.ByteStream(b'{"error":{"message":"invalid input","type":"BadRequestError"}}'))
        async with client_for(configuration(tmp_path), upstream) as (client, router):
            response = await client.post("/v1/chat/completions", json={})
            assert response.status_code == 400 and response.json()["error"]["type"] == "BadRequestError"
            assert calls == [18049]
            assert all(w.active == 0 for w in router.workers.values())
    asyncio.run(scenario())


def test_stream_failure_after_output_never_replays_and_releases_slot(tmp_path):
    async def scenario():
        calls = []
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            calls.append(request.url.port)
            gate = asyncio.Event(); gate.set()
            return httpx.Response(200, stream=GatedStream(gate, (b'data: {"token":"first"}\n\n',),
                                                         failure=httpx.ReadError("sensitive upstream detail")))
        async with client_for(configuration(tmp_path), upstream) as (client, router):
            with pytest.raises(RuntimeError, match="stream interrupted; request was not replayed"):
                await client.post("/v1/chat/completions", json={})
            assert calls == [18049]
            assert all(w.active == 0 for w in router.workers.values())
    asyncio.run(scenario())


def test_atomic_reconfiguration_drains_removed_worker_without_interrupting(tmp_path):
    async def scenario():
        gate = asyncio.Event(); calls = []
        async def upstream(request):
            if request.url.path == "/health": return httpx.Response(200)
            calls.append(request.url.port)
            if request.url.port == 18049: return httpx.Response(200, stream=GatedStream(gate))
            return mock_json(200, {"worker": request.url.port})
        path = configuration(tmp_path, ports=(18049,))
        async with client_for(path, upstream) as (client, router):
            old = asyncio.create_task(client.post("/v1/chat/completions", json={}))
            await until(lambda: len(calls) == 1)
            replacement = tmp_path / "replace.json"
            replacement.write_text(json.dumps({"upstreams": ["http://127.0.0.1:18050/v1"]}))
            replacement.replace(path)
            response = await client.post("/v1/chat/completions", json={})
            assert response.json() == {"worker": 18050}
            assert router.workers["http://127.0.0.1:18049"].active == 1
            assert router.workers["http://127.0.0.1:18049"].enabled is False
            gate.set(); await old
            assert all(w.active == 0 for w in router.workers.values())
    asyncio.run(scenario())


def test_health_and_fixed_paths(tmp_path):
    async def scenario():
        async def upstream(request): return httpx.Response(503)
        async with client_for(configuration(tmp_path), upstream) as (client, router):
            assert (await client.get("/health")).status_code == 503
            assert (await client.post("/v1/chat/completions", json={})).status_code == 503
            assert (await client.get("/metrics")).status_code == 404
            assert (await client.get("/openapi.json")).status_code == 404
            assert (await client.post("/v1/models", json={})).status_code == 405
    asyncio.run(scenario())


@pytest.mark.parametrize("url", ["http://example.com:8000/v1", "http://10.123.0.41:8000/v1",
    "https://127.0.0.1:18049/v1", "http://user:secret@127.0.0.1:18049/v1",
    "http://127.0.0.1:18049/other", "http://127.0.0.1:18048/v1", "http://127.0.0.1:18049/v1?x=y"])
def test_configuration_rejects_nonlocal_or_ambiguous_upstreams(tmp_path, url):
    path = tmp_path / "bad.json"; path.write_text(json.dumps({"upstreams": [url]}))
    with pytest.raises(ValueError): module.load_config(path)


def test_real_loopback_controller_and_disconnected_client(tmp_path):
    """Real TCP transport and uvicorn; the backend is a CPU-only HTTP fixture."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import os
    import socket
    import subprocess
    import threading
    import time
    import urllib.request

    entered, release = threading.Event(), threading.Event()
    received = []

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def do_GET(self):
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            received.append(body)
            if json.loads(body).get("wait"):
                entered.set(); release.wait(10)
            try:
                self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True); thread.start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    control = root / "deploy/model-router-control.sh"
    config = configuration(tmp_path, ports=(backend.server_port,))
    pidfile = tmp_path / "control.pid"
    env = {**os.environ, "FORECASTLAB_ROUTER_CONFIG": str(config), "FORECASTLAB_ROUTER_PORT": str(port),
           "FORECASTLAB_ROUTER_PIDFILE": str(pidfile), "FORECASTLAB_ROUTER_LOGFILE": str(tmp_path / "control.log")}

    def command(action, expected=0):
        result = subprocess.run(["bash", str(control), action], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == expected, (result.stdout, result.stderr)
        return result

    def health():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return json.load(response)

    async def cancel_client():
        async with httpx.AsyncClient(trust_env=False) as client:
            job = asyncio.create_task(client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json={"wait": True}))
            async with asyncio.timeout(3):
                while not entered.is_set(): await asyncio.sleep(.01)
            job.cancel()
            with suppress(asyncio.CancelledError): await job

    try:
        assert "started" in command("start").stdout
        saved_pid = pidfile.read_text()
        assert "already running" in command("start").stdout
        assert pidfile.read_text() == saved_pid
        assert json.loads(command("status").stdout)["ok"]
        body = {"model": "cpu-fixture", "chat_template_kwargs": {"enable_thinking": True}, "usage": {"total_tokens": 5}}
        with httpx.Client(trust_env=False) as client:
            response = client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body)
            assert response.json() == body
        asyncio.run(cancel_client())
        deadline = time.monotonic() + 3
        while health()["workers"][0]["active_generations"]:
            assert time.monotonic() < deadline
            time.sleep(.05)
        release.set()
        command("stop"); assert not pidfile.exists()
        command("status", 1)
        # A record naming this pytest process must never be replaced/signalled.
        pidfile.write_text(str(os.getpid()))
        command("start", 1); command("stop", 1)
        assert pidfile.read_text() == str(os.getpid())
        log = (tmp_path / "control.log").read_text()
        assert "enable_thinking" not in log and "cpu-fixture" not in log
    finally:
        release.set()
        if pidfile.exists() and pidfile.read_text() != str(os.getpid()):
            subprocess.run(["bash", str(control), "stop"], env=env, capture_output=True, timeout=20)
        backend.shutdown(); backend.server_close(); thread.join(timeout=2)
