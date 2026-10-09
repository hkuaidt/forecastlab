"""Single model transport with durable reservations and bounded repair attempts."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager, suppress
import hashlib
import math
import logging
import httpx
import json
import os
import threading
import time
from typing import Callable
from uuid import uuid4
from openai import OpenAI, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError
from . import config
from .schemas import ModelCallRecord
from .model_context import LocalTokenCounter, fit_local_context, model_messages


class BudgetExceeded(RuntimeError):
    pass


class ModelCancelled(RuntimeError):
    """Explicit run cancellation; callers must not turn this into a report fallback."""


class ModelRequestTimeout(RuntimeError):
    """The whole complete() deadline, including retries and queueing, expired."""


class CancellableTransport(httpx.BaseTransport):
    """Bridge the sync SDK to cancellable async I/O, without abandoned request threads.

    Each calling actor has its own deadline checker and async connection lifetime.
    Cancelling closes the upstream socket, allowing the router/vLLM to abort work.
    """
    def __init__(self, *, trust_env: bool = True):
        self.local = threading.local()
        self.trust_env = trust_env

    @contextmanager
    def limits(self, check):
        previous = getattr(self.local, "check", None)
        self.local.check = check
        try:
            yield
        finally:
            self.local.check = previous

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check = getattr(self.local, "check", None)
        if check is None:
            raise RuntimeError("Model HTTP request requires an active deadline")

        async def exchange():
            remaining = check()
            caps = request.extensions.get("timeout", {})
            timeout = httpx.Timeout(**{key: min(remaining, caps.get(key) or remaining)
                                       for key in ("connect", "read", "write", "pool")})
            async with httpx.AsyncClient(trust_env=self.trust_env,
                    timeout=timeout, follow_redirects=True) as client:
                outgoing = client.build_request(request.method, request.url,
                    headers=request.headers, content=request.read())
                response = await client.send(outgoing, stream=True)
                try:
                    headers = response.headers
                    if response.is_stream_consumed:
                        raw = response.content
                        headers = [(key, value) for key, value in response.headers.multi_items()
                                   if key.lower() not in {"content-encoding", "content-length"}]
                    else:
                        raw = b"".join([chunk async for chunk in response.aiter_raw()])
                    return httpx.Response(response.status_code, headers=headers,
                        stream=httpx.ByteStream(raw), extensions=response.extensions)
                finally:
                    await response.aclose()

        async def bounded():
            task = asyncio.create_task(exchange())
            try:
                while not task.done():
                    remaining = check()
                    await asyncio.wait({task}, timeout=min(.05, remaining))
                check()
                return await task
            finally:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        return asyncio.run(bounded())


def unique_request_count(records: list[ModelCallRecord]) -> int:
    return len({r.request_id for r in records})


def request_active_seconds(records: list[ModelCallRecord]) -> float:
    """Union of measured request intervals: repeated IDs and parallel calls count once."""
    unique = {r.request_id: r for r in records}
    intervals = sorted((r.started_at.timestamp(), r.started_at.timestamp()+r.elapsed_seconds) for r in unique.values())
    total = 0.0
    start = end = None
    for lo, hi in intervals:
        if start is None:
            start, end = lo, hi
        elif lo <= end:
            end = max(end, hi)
        else:
            total += end-start
            start, end = lo, hi
    return total + (end-start if start is not None else 0)


def remaining_run_budget(preparation_records, runtime_records, *, max_calls: int) -> int:
    return max(0, max_calls - unique_request_count([*preparation_records, *runtime_records]))


class ModelClient:
    def __init__(self, *, initial_usage: dict[str, int] | None = None,
                 initial_active_seconds: float = 0, call_limit: int | None = None,
                 on_reserve: Callable[[str, str], ModelCallRecord] | None = None,
                 on_finish: Callable[[ModelCallRecord], None] | None = None,
                 cancel_event: threading.Event | None = None, deadline_monotonic: float | None = None):
        if not config.MODEL_API_KEY:
            raise ValueError("请在项目 .env 中设置 QWEN_API_KEY 或 DEEPSEEK_API_KEY；或运行教学回放。")
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.initial_active_seconds = max(0, initial_active_seconds)
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self.deadline_monotonic = self.started + max(0, config.MAX_SECONDS - self.initial_active_seconds)
        if deadline_monotonic is not None:
            self.deadline_monotonic = min(self.deadline_monotonic, deadline_monotonic)
        self.request_timeout = float(os.getenv("FORECASTLAB_MODEL_TIMEOUT", "45"))
        if not math.isfinite(self.request_timeout) or self.request_timeout <= 0:
            raise ValueError("FORECASTLAB_MODEL_TIMEOUT must be a positive finite duration")
        self.transport = CancellableTransport(trust_env=not config.MODEL_BASE_URL.startswith(
            ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")))
        self.http_client = httpx.Client(transport=self.transport, trust_env=False)
        self.client = OpenAI(api_key=config.MODEL_API_KEY, base_url=config.MODEL_BASE_URL,
            timeout=self.request_timeout, max_retries=0, http_client=self.http_client)
        self.call_limit = config.MAX_CALLS if call_limit is None else max(0, call_limit)
        self.on_reserve, self.on_finish = on_reserve, on_finish
        self.actual_model: str | None = None
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        if initial_usage:
            self.usage.update({k: max(0, int(initial_usage.get(k, 0))) for k in self.usage})
        self.call_records: list[ModelCallRecord] = []
        self.context_counter = None
        if config.MODEL_BASE_URL.startswith(("http://127.0.0.1:", "http://localhost:")):
            self.context_counter = LocalTokenCounter(config.MODEL_BASE_URL, config.MODEL_NAME,
                enable_thinking=os.getenv("FORECASTLAB_ENABLE_THINKING", "false").strip().lower() in {"1", "true", "yes"},
                max_model_len=int(os.getenv("FORECASTLAB_CONTEXT_WINDOW", "16384")))

    @property
    def active_seconds(self) -> float:
        # Each analysis request constructs its own client; human editing waits are excluded.
        return self.initial_active_seconds + max(0, time.monotonic() - self.started)

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise ModelCancelled("运行已取消")

    def _check_limits(self, deadline: float) -> float:
        self.check_cancelled()
        now = time.monotonic()
        if now >= self.deadline_monotonic:
            raise BudgetExceeded("模型调用总活动时限已达到上限")
        if now >= deadline:
            raise ModelRequestTimeout("模型请求总时限已达到上限（含排队与重试）")
        return min(deadline, self.deadline_monotonic) - now

    def _wait_retry(self, seconds: float, deadline: float):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            remaining = self._check_limits(deadline)
            self.cancel_event.wait(min(.05, remaining, max(0, until-time.monotonic())))

    def _count_tokens(self, messages, deadline):
        # Tokenization is CPU-only but must share cancellation and the total deadline.
        counter = self.context_counter
        if not isinstance(counter, LocalTokenCounter):
            return counter(messages)
        if counter.available:
            try:
                remaining = self._check_limits(deadline)
                response = self.http_client.post(counter.url,
                    json={"model": counter.model, "messages": messages, "add_generation_prompt": True,
                          "chat_template_kwargs": {"enable_thinking": counter.enable_thinking}},
                    timeout=min(5, remaining))
                response.raise_for_status()
                body = response.json()
                count, window = body.get("count"), body.get("max_model_len")
                if type(count) is not int or count < 0:
                    raise ValueError("Invalid tokenizer count")
                if type(window) is int and window > 0:
                    counter.max_model_len = min(counter.max_model_len, window)
                return count
            except (ModelCancelled, ModelRequestTimeout, BudgetExceeded):
                raise
            except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError):
                counter.available = False
        self._check_limits(deadline)
        from .model_context import conservative_token_count
        return conservative_token_count(messages)

    def complete(self, role: str, payload: dict, schema: type[BaseModel], instructions: str,
                 *, attempt_limit: int | None = None) -> BaseModel:
        timeout = float(os.getenv("FORECASTLAB_REPORT_TIMEOUT", str(self.request_timeout))) if role == "forecast" else self.request_timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("FORECASTLAB_REPORT_TIMEOUT must be a positive finite duration")
        deadline = min(self.deadline_monotonic, time.monotonic() + timeout)
        with self.transport.limits(lambda: self._check_limits(deadline)):
            return self._complete(role, payload, schema, instructions, attempt_limit=attempt_limit, deadline=deadline)

    def _complete(self, role: str, payload: dict, schema: type[BaseModel], instructions: str,
                  *, attempt_limit: int | None, deadline: float) -> BaseModel:
        total_attempts = 3 if attempt_limit is None else max(1, min(3, attempt_limit))
        error = None
        for attempt in range(total_attempts):
            self._check_limits(deadline)
            output_tokens = int(os.getenv("FORECASTLAB_MAX_OUTPUT_TOKENS", str((8000 if role in {"review", "forecast", "evidence", "evidence12"} else 3000) + attempt * 1000)))
            messages = model_messages(role, payload, schema.model_json_schema(), instructions, repair_feedback=error)
            if self.context_counter is not None:
                fitted = fit_local_context(payload, schema=schema.model_json_schema(), instructions=instructions,
                    role=role, max_model_len=self.context_counter.max_model_len,
                    max_output_tokens=output_tokens, token_counter=lambda messages: self._count_tokens(messages, deadline), repair_feedback=error)
                messages = fitted.messages
            request_fingerprint = f"{config.MODEL_PROVIDER}|{config.MODEL_NAME}|temperature={config.MODEL_TEMPERATURE}|{role}|{json.dumps(messages, ensure_ascii=False)}"
            digest = hashlib.sha256(request_fingerprint.encode()).hexdigest()
            with self.lock:
                self._check_limits(deadline)
                if self.usage["calls"] >= self.call_limit or self.active_seconds >= config.MAX_SECONDS:
                    raise BudgetExceeded("模型调用或总活动时限已达到上限")
                # Persist BEFORE issuing the HTTP request; unknown-token failures still spend a call.
                record = (self.on_reserve(digest, "agent12-v1") if self.on_reserve else
                          ModelCallRecord(request_id=f"request_{uuid4().hex}", owner_id="unbound",
                                          phase="runtime", input_hash=digest, attempt=attempt+1))
                record.model = config.MODEL_NAME
                self.call_records.append(record)
                self.usage["calls"] += 1
            started = time.monotonic()
            retryable = False
            try:
                kwargs = dict(
                    model=config.MODEL_NAME,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=config.MODEL_TEMPERATURE,
                    max_tokens=output_tokens,
                )
                thinking = os.getenv("FORECASTLAB_ENABLE_THINKING", "false").strip().lower() in {"1", "true", "yes"}
                if config.MODEL_BASE_URL.startswith(("http://127.0.0.1:", "http://localhost:")):
                    kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": thinking}}
                    kwargs["temperature"] = 0
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
                    }
                    if os.getenv("FORECASTLAB_LOCAL_JSON_MODE") == "prompt":
                        kwargs.pop("response_format")
                elif config.MODEL_NAME == "qwen3.8-flash":
                    kwargs["extra_body"] = {"enable_thinking": thinking}
                elif config.MODEL_PROVIDER == "deepseek":
                    # DeepSeek V4.1 Flash enables high-effort thinking by default.
                    # Structured ForecastLab agents need concise JSON, not hidden reasoning.
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                response = self.client.chat.completions.create(**kwargs)
                self._check_limits(deadline)
                record.model = getattr(response, "model", None) or config.MODEL_NAME
                self.actual_model = record.model
                if response.usage:
                    record.usage_known = True
                    record.prompt_tokens = response.usage.prompt_tokens or 0
                    record.completion_tokens = response.usage.completion_tokens or 0
                    with self.lock:
                        self.usage["prompt_tokens"] += record.prompt_tokens
                        self.usage["completion_tokens"] += record.completion_tokens
                if response.choices[0].finish_reason == "length":
                    raise ValueError("模型输出达到 token 上限")
                result = schema.model_validate_json(response.choices[0].message.content or "")
                record.status = "succeeded"
                return result
            except (ModelCancelled, ModelRequestTimeout, BudgetExceeded) as exc:
                record.status = "interrupted" if isinstance(exc, ModelCancelled) else "failed"
                record.error_type = type(exc).__name__
                raise
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                record.status, record.error_type = "failed", type(exc).__name__
                error = (json.dumps(exc.errors(include_input=False, include_url=False), ensure_ascii=False, default=str)[:500]
                         if isinstance(exc, ValidationError) else str(exc)[:500])
                logging.getLogger(__name__).warning("%s output validation: %s", role, error)
                retryable = attempt < min(1, total_attempts - 1)
                if not retryable:
                    raise RuntimeError(f"{role}输出无法通过结构校验：{error}") from exc
            except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as exc:
                # The SDK wraps transport cancellation as APIConnectionError.
                try:
                    self._check_limits(deadline)
                except (ModelCancelled, ModelRequestTimeout, BudgetExceeded) as stopped:
                    record.status = "interrupted" if isinstance(stopped, ModelCancelled) else "failed"
                    record.error_type = type(stopped).__name__
                    raise
                record.status, record.error_type = "failed", type(exc).__name__
                error = f"请求失败：{type(exc).__name__}"
                retryable = attempt < total_attempts - 1
                if not retryable:
                    # Never expose provider exception strings containing credentials or response bodies.
                    raise RuntimeError(f"{role}调用失败：{type(exc).__name__}") from exc
            except Exception as exc:
                record.status, record.error_type = "failed", type(exc).__name__
                raise RuntimeError(f"{role}调用失败：{type(exc).__name__}") from exc
            finally:
                record.elapsed_seconds = max(0, time.monotonic() - started)
                if record.status == "reserved":
                    record.status, record.error_type = "interrupted", "RequestInterrupted"
                if self.on_finish:
                    self.on_finish(record)
            if retryable and record.error_type in {"APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"}:
                self._wait_retry(2 ** attempt, deadline)
        raise RuntimeError(f"{role}输出无法通过结构校验：{error}")
