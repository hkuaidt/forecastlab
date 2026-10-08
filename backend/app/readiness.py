"""Cheap, cached dependency readiness; never performs a paid search or generation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
import httpx
from . import config


def model_readiness():
    if not config.MODEL_API_KEY:
        return {"status": "unconfigured", "detail": "尚未配置模型"}
    base = config.MODEL_BASE_URL.rstrip("/")
    try:
        with httpx.Client(timeout=2.5, trust_env=False) as client:
            response = client.get(base + "/models", headers={"Authorization": f"Bearer {config.MODEL_API_KEY}"})
            response.raise_for_status()
            ids = {item.get("id") for item in response.json().get("data", [])}
            if config.MODEL_NAME not in ids:
                return {"status": "unavailable", "detail": "模型服务可连接，但目标模型未就绪"}
            if urlsplit(base).hostname in {"127.0.0.1", "localhost"}:
                health = client.get(base.removesuffix("/v1") + "/health")
                if health.status_code != 404:
                    health.raise_for_status()
                    workers = health.json().get("workers", [])
                    if workers:
                        ready = sum(bool(w.get("healthy")) and w.get("enabled", True) for w in workers)
                        expected = sum(w.get("enabled", True) for w in workers)
                        return {"status": "ready" if ready == expected else "degraded" if ready else "unavailable",
                                "detail": f"{ready}/{expected} 个推理服务可用", "workers_ready": ready, "workers_total": expected}
        return {"status": "ready", "detail": "目标模型已就绪"}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {"status": "unavailable", "detail": "暂时无法连接目标模型，请检查推理服务"}


def search_readiness():
    if not (config.BRAVE_SEARCH_API_KEY or config.TAVILY_API_KEY):
        return {"status": "unconfigured", "detail": "尚未配置联网搜索"}
    origin = "https://api.search.brave.com" if config.BRAVE_SEARCH_API_KEY else "https://api.tavily.com"
    try:
        with httpx.Client(timeout=2.5, proxy=config.SEARCH_PROXY or None, trust_env=False) as client:
            response = client.get(origin)
            if response.status_code >= 500 or response.status_code == 407:
                return {"status": "unavailable", "detail": "搜索服务或代理暂不可用"}
        # A reachable origin does not establish key validity or remaining quota.
        return {"status": "unknown", "transport_ready": True, "detail": "搜索网络可达；密钥和额度将在取证时验证"}
    except httpx.HTTPError:
        return {"status": "unavailable", "detail": "无法连接搜索服务或代理"}


class ReadinessCache:
    def __init__(self, ttl=15):
        self.ttl = ttl
        self.lock = Lock()
        self.checked_at = float("-inf")
        self.cached = None

    def get(self):
        with self.lock:
            if self.cached is None or monotonic() - self.checked_at >= self.ttl:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    model = pool.submit(model_readiness)
                    search = pool.submit(search_readiness)
                    self.cached = {"model": model.result(), "search": search.result()}
                self.checked_at = monotonic()
            return self.cached
