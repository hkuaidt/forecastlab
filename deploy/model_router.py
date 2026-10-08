"""Loopback OpenAI proxy, one generation per worker; no payload/header logging.

FORECASTLAB_ROUTER_CONFIG defaults to .forecastlab/model-router.json. Replace
that file atomically to reload it without restarting or interrupting requests:
{"upstreams": ["http://127.0.0.1:18049/v1", "http://127.0.0.1:18050/v1"],
 "queue_limit": 32, "queue_timeout_seconds": 120, "read_timeout_seconds": 600}
Run exactly one uvicorn worker (the control script enforces this).
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import ipaddress
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx


ROOT = Path(__file__).resolve().parents[1]
HOP_HEADERS = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
               b"te", b"trailer", b"transfer-encoding", b"upgrade"}
GENERATION_PATHS = {"/v1/chat/completions", "/v1/completions"}


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("upstreams"), list) or not data["upstreams"]:
        raise ValueError("Configuration requires a nonempty upstream list")
    origins = []
    for value in data["upstreams"]:
        if not isinstance(value, str):
            raise ValueError("Upstreams must be URL strings")
        url = urlsplit(value)
        if (url.scheme != "http" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/", "/v1", "/v1/")
                or not url.port or not ipaddress.ip_address(url.hostname).is_loopback):
            raise ValueError("Only literal loopback HTTP upstreams are allowed")
        if url.port == int(os.getenv("FORECASTLAB_ROUTER_PORT", "18048")):
            raise ValueError("An upstream cannot use the router port")
        origin = f"http://{url.netloc}"
        if origin in origins:
            raise ValueError("Duplicate upstream")
        origins.append(origin)
    if len(origins) > 16:
        raise ValueError("Too many upstreams")
    config = {"upstreams": origins}
    limit = data.get("queue_limit", 32)
    if type(limit) is not int or not 0 <= limit <= 1024:
        raise ValueError("queue_limit must be an integer between 0 and 1024")
    config["queue_limit"] = limit
    for key, default in (("queue_timeout_seconds", 120), ("connect_timeout_seconds", 3),
                         ("read_timeout_seconds", 600), ("health_interval_seconds", 5),
                         ("health_timeout_seconds", 2)):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 86400:
            raise ValueError(f"Invalid {key}")
        config[key] = value
    return config


def forwarded_headers(headers, *, request=False):
    blocked = HOP_HEADERS | ({b"host", b"content-length"} if request else set())
    for key, value in headers:
        if key.lower() == b"connection":
            blocked = blocked | {token.strip().lower() for token in value.split(b",")}
    return [(key, value) for key, value in headers if key.lower() not in blocked]


@dataclass
class Worker:
    origin: str
    enabled: bool = True
    healthy: bool = False
    active: int = 0
    started: int = 0
    last_used: float = 0
    next_probe: float = 0
    probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class OpenResponse:
    router: "Router"
    worker: Worker
    response: httpx.Response
    generation: bool
    closed: bool = False

    async def close(self):
        if self.closed:
            return
        self.closed = True
        # Starlette cancels the response task when a streaming client leaves.
        with anyio.CancelScope(shield=True):
            try:
                await self.response.aclose()
            finally:
                if self.generation:
                    await self.router.release(self.worker)


class ProxyResponse(StreamingResponse):
    def __init__(self, opened: OpenResponse):
        self.opened = opened
        super().__init__(self.body_chunks(), status_code=opened.response.status_code)
        self.raw_headers = forwarded_headers(opened.response.headers.raw)

    async def body_chunks(self):
        try:
            async for chunk in self.opened.response.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            raise RuntimeError("Model upstream stream interrupted; request was not replayed") from None

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.opened.close()


class Router:
    def __init__(self, path: Path, transport=None):
        self.path, self.signature = path, None
        self.config: dict = {}
        self.workers: dict[str, Worker] = {}
        self.waiters: deque = deque()
        self.changed = asyncio.Condition()
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=transport,
                                       limits=httpx.Limits(max_connections=64, max_keepalive_connections=16))

    async def refresh(self):
        # Atomic rename is the supported writer protocol. An invalid update
        # fails visibly; it cannot replace the last validated worker state.
        try:
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature != self.signature:
                config = load_config(self.path)
                async with self.changed:
                    for worker in self.workers.values():
                        worker.enabled = worker.origin in config["upstreams"]
                    for origin in config["upstreams"]:
                        self.workers.setdefault(origin, Worker(origin)).enabled = True
                    self.config, self.signature = config, signature
                    self.changed.notify_all()
        except (OSError, ValueError, TypeError):
            raise HTTPException(503, "Model router configuration is unavailable or invalid") from None
        await asyncio.gather(*(self.probe(w) for w in list(self.workers.values()) if w.enabled))

    async def probe(self, worker: Worker):
        async with worker.probe_lock:
            if not worker.enabled or worker.next_probe > time.monotonic():
                return
            started = time.monotonic()
            try:
                response = await self.client.get(worker.origin + "/health", timeout=self.config["health_timeout_seconds"])
                healthy = response.status_code == 200
            except httpx.HTTPError:
                healthy = False
            async with self.changed:
                if worker.next_probe > started:
                    return  # A concurrent request has since failed to connect.
                worker.healthy = healthy
                worker.next_probe = time.monotonic() + self.config["health_interval_seconds"]
                self.changed.notify_all()

    async def monitor(self):
        while True:
            await asyncio.sleep(min(self.config.get("health_interval_seconds", 5), 1))
            with suppress(HTTPException):
                await self.refresh()

    async def acquire(self, generation: bool, excluded: set[str], deadline: float) -> Worker:
        token = None
        async with self.changed:
            try:
                while True:
                    available = [w for w in self.workers.values() if w.enabled and w.healthy and w.origin not in excluded]
                    if not available:
                        raise HTTPException(503, "No healthy model worker is available")
                    idle = [w for w in available if not generation or w.active == 0]
                    if idle and (not generation or not self.waiters or self.waiters[0] is token):
                        worker = min(idle, key=lambda w: (w.active, w.last_used))
                        if generation:
                            worker.active = 1
                            worker.started += 1
                            worker.last_used = time.monotonic()
                        return worker
                    if token is None:
                        if len(self.waiters) >= self.config["queue_limit"]:
                            raise HTTPException(429, "Model generation queue is full")
                        token = object()
                        self.waiters.append(token)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HTTPException(504, "Timed out waiting for an idle model worker")
                    try:
                        await asyncio.wait_for(self.changed.wait(), remaining)
                    except TimeoutError:
                        raise HTTPException(504, "Timed out waiting for an idle model worker") from None
            finally:
                if token is not None:
                    self.waiters.remove(token)
                    self.changed.notify_all()

    async def release(self, worker: Worker):
        async with self.changed:
            worker.active = 0
            self.changed.notify_all()

    async def open(self, request: Request, body: bytes) -> OpenResponse:
        generation = request.url.path in GENERATION_PATHS
        excluded: set[str] = set()
        deadline = time.monotonic() + self.config["queue_timeout_seconds"]
        while True:
            worker = await self.acquire(generation, excluded, deadline)
            response = None
            try:
                url = httpx.URL(worker.origin + request.url.path).copy_with(query=request.scope["query_string"])
                outgoing = self.client.build_request(request.method, url, content=body,
                    headers=forwarded_headers(request.headers.raw, request=True),
                    timeout=httpx.Timeout(self.config["read_timeout_seconds"], connect=self.config["connect_timeout_seconds"]))
                response = await self.client.send(outgoing, stream=True)
                return OpenResponse(self, worker, response, generation)
            except BaseException as exc:
                connection_failed = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                with anyio.CancelScope(shield=True):
                    if response is not None:
                        await response.aclose()
                    if connection_failed:
                        async with self.changed:
                            worker.healthy = False
                            worker.next_probe = time.monotonic() + self.config["health_interval_seconds"]
                            if generation:
                                worker.active = 0
                            self.changed.notify_all()
                    elif generation:
                        await self.release(worker)
                # Only connection establishment failures guarantee that no
                # request bytes reached the backend. Never replay read/write
                # errors, HTTP errors, or interrupted generated streams.
                if connection_failed:
                    excluded.add(worker.origin)
                    continue
                if isinstance(exc, httpx.HTTPError):
                    raise HTTPException(502, "Model upstream request failed; request was not replayed") from None
                raise

    async def snapshot(self):
        async with self.changed:
            rows = [{"url": w.origin + "/v1", "enabled": w.enabled, "healthy": w.healthy,
                     "active_generations": w.active, "generations_started": w.started}
                    for w in self.workers.values() if w.enabled or w.active]
            return {"ok": any(row["enabled"] and row["healthy"] for row in rows),
                    "queued_generations": len(self.waiters), "queue_limit": self.config["queue_limit"], "workers": rows}


def create_app(config_path: Path | None = None, *, transport=None):
    config_file = config_path or Path(os.getenv("FORECASTLAB_ROUTER_CONFIG", str(ROOT / ".forecastlab/model-router.json")))

    @asynccontextmanager
    async def lifespan(app):
        router = Router(config_file, transport)
        app.state.router = router
        monitor = None
        try:
            await router.refresh()
            monitor = asyncio.create_task(router.monitor())
            yield
        finally:
            if monitor:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
            await router.client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        router = app.state.router
        await router.refresh()
        body = await router.snapshot()
        return JSONResponse(body, status_code=200 if body["ok"] else 503)

    async def forward(request: Request):
        router = app.state.router
        await router.refresh()
        body = await request.body()
        work = asyncio.create_task(router.open(request, body))
        try:
            # A disconnected client waiting in the queue or waiting for first
            # response headers must also release its reservation promptly.
            while not work.done():
                await asyncio.wait({work}, timeout=.1)
                if not work.done() and await request.is_disconnected():
                    raise HTTPException(499, "Client disconnected")
            return ProxyResponse(await work)
        except BaseException:
            work.cancel()
            with suppress(BaseException):
                await work
            if not work.cancelled() and work.exception() is None:
                await work.result().close()
            raise

    for route_path in GENERATION_PATHS | {"/tokenize", "/detokenize"}:
        app.add_api_route(route_path, forward, methods=["POST"])
    app.add_api_route("/v1/models", forward, methods=["GET"])
    return app


app = create_app()
