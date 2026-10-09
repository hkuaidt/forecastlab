"""Tool-owned evidence metadata. Models never create source URLs or hashes."""
import asyncio
import hashlib
import html
import threading
import ipaddress
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import math
import re
import time
import httpx
from . import config
from .schemas import Evidence, ImportedEvidence, QuestionSpec, utcnow


def public_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def normalize_import(items: list[ImportedEvidence], question: QuestionSpec) -> list[Evidence]:
    if len(items) > 20:
        raise ValueError("每个证据包最多 20 条")
    seen = set()
    out = []
    for i, item in enumerate(items, 1):
        if item.id and item.id in seen:
            raise ValueError(f"重复证据编号：{item.id}")
        if item.id:
            seen.add(item.id)
        if item.source_url and not public_url(str(item.source_url)):
            raise ValueError(f"来源地址不是公开 HTTP(S) URL：{item.id}")
        if item.published_at and item.published_at > question.as_of:
            raise ValueError(f"证据晚于信息截至时间：{item.id}")
        if item.updated_at and item.updated_at > question.as_of:
            raise ValueError(f"证据更新晚于信息截至时间：{item.id}")
        retrieved_at = item.retrieved_at or utcnow()
        if item.source_type != "exercise" and retrieved_at > question.as_of and (utcnow() - question.as_of).days > 1:
            raise ValueError(f"历史问题需要截点前冻结的证据快照：{item.id}")
        out.append(Evidence(
            id=f"E{i:03}", source_url=item.source_url, file_id=item.file_id,
            title=item.title, publisher=item.publisher, published_at=item.published_at,
            updated_at=item.updated_at, retrieved_at=retrieved_at, event_at=item.event_at,
            excerpt=item.excerpt, claim=item.claim, snapshot_path=item.snapshot_path,
            content_hash=hashlib.sha256(item.excerpt.encode()).hexdigest(),
            source_type=item.source_type,
            source_group=item.source_group or (urlparse(str(item.source_url)).hostname if item.source_url else item.file_id or "local"),
            date_status=item.date_status if item.source_type == "exercise" or item.snapshot_path else "unknown",
        ))
    return out


@dataclass
class SourceCandidate:
    url: str
    title: str
    text: str
    has_body: bool
    score: float
    query_ids: list[str]
    aliases: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    event_at: datetime | None = None
    source_group: str = ""
    source_group_basis: str = ""
    possible_same_source: list[str] = field(default_factory=list)


def canonical_source_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def _parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("/", "-"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


_brave_lock = threading.Lock()
_brave_last_request = 0.0


def _search_one(query: str, *, days: int = 90, as_of=None) -> list[dict]:
    """Read a bounded streaming body; do not download unlimited text then truncate."""
    as_of = as_of or utcnow()
    freshness = f"{(as_of-timedelta(days=days)):%Y-%m-%d}to{as_of:%Y-%m-%d}"
    if config.BRAVE_SEARCH_API_KEY:
        global _brave_last_request
        with _brave_lock:
            time.sleep(max(0.0, 1.1 - (time.monotonic() - _brave_last_request)))
            _brave_last_request = time.monotonic()
        with httpx.Client(timeout=25, proxy=config.SEARCH_PROXY or None) as client:
            with client.stream(
                "GET", "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": config.BRAVE_SEARCH_API_KEY, "Accept": "application/json"},
                params={"q": query[:400], "count": 8, "extra_snippets": "true", "freshness": freshness},
            ) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > 8 * 1024 * 1024:
                        raise ValueError("检索响应超过8 MiB限制")
                    content.extend(chunk)
                payload = json.loads(content)
        results = payload.get("web", {}).get("results", [])
        if not isinstance(results, list):
            raise ValueError("检索响应缺少资料数组")
        return [
            {"url": row.get("url", ""), "title": row.get("title", ""),
             "content": html.unescape(re.sub(r"<[^>]+>", "", "\n".join(
                 [row.get("description") or "",
                  *[value for value in row.get("extra_snippets", []) if isinstance(value, str)]]))),
             "page_age": row.get("page_age"), "search_window_days": days,
             "score": max(.1, 1 - i * .08)}
            for i, row in enumerate(results[:8]) if isinstance(row, dict)
        ]
    with httpx.Client(timeout=25) as client:
        with client.stream("POST", "https://api.tavily.com/search", json={
            "api_key": config.TAVILY_API_KEY, "query": query,
            "search_depth": "basic", "max_results": 8,
            "include_raw_content": "text", "include_answer": False,
            "start_date": (as_of-timedelta(days=days)).strftime("%Y-%m-%d"), "end_date": as_of.strftime("%Y-%m-%d"),
        }) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > 8 * 1024 * 1024:
                    raise ValueError("检索响应超过8 MiB限制")
                content.extend(chunk)
            payload = json.loads(content)
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("检索响应缺少资料数组")
    return [{**r, "search_window_days": days} for r in results[:8] if isinstance(r, dict)]


def group_sources(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    """Only exact duplicates merge; suspected reprints remain visible."""
    by_url, by_hash, unique = {}, {}, []
    for c in candidates:
        canonical = canonical_source_url(c.url)
        digest = hashlib.sha256(c.text.replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()
        existing = by_url.get(canonical) or by_hash.get(digest)
        if existing:
            existing.query_ids = list(dict.fromkeys(existing.query_ids + c.query_ids))
            existing.score = max(existing.score, c.score)
            for alias in c.aliases:
                if alias not in existing.aliases:
                    existing.aliases.append(alias)
            by_url[canonical] = existing
            by_hash[digest] = existing
        else:
            by_url[canonical] = by_hash[digest] = c
            unique.append(c)
    roots = {}
    for c in unique:
        match = re.search(r"(?:转载自|原文来源|原文链接|Originally published at|Source URL)\s*[:：]?\s*(https?://[^\s<>)]+)", c.text, re.I)
        if match and public_url(match.group(1)):
            roots[c.url] = (canonical_source_url(match.group(1).rstrip("。，；")), match.group(0))
    declared_roots = {root for root, _ in roots.values()}
    for c in unique:
        canonical = canonical_source_url(c.url)
        root, basis = roots.get(c.url, (canonical, "单独资料，未核实独立性"))
        if canonical in declared_roots and c.url not in roots:
            basis = "其他材料明确引用此原始来源链接，内容仍需核查"
        c.source_group = "source:" + hashlib.sha256(root.encode()).hexdigest()[:16]
        c.source_group_basis = basis
    # This is a conservative warning, never an independence certificate.
    grams = {}
    for i, c in enumerate(unique):
        text = re.sub(r"\s+", "", c.text[:200_000])
        if len(text) >= 200:
            grams[i] = {text[j:j+5] for j in range(len(text)-4)}
    for i, left in enumerate(unique):
        for j in range(i+1, len(unique)):
            right = unique[j]
            if i not in grams or j not in grams or left.source_group == right.source_group:
                continue
            union = grams[i] | grams[j]
            if union and len(grams[i] & grams[j]) / len(union) >= .85:
                left.possible_same_source.append(right.url)
                right.possible_same_source.append(left.url)
    return unique


def _recency_rank(candidate, as_of):
    date = candidate.published_at or _parse_date(candidate.metadata.get("page_age"))
    if date:
        age = (as_of - date).days
        return (0 if candidate.published_at else 1) if 0 <= age <= 90 else 2 if age <= 365 else 3
    return 1 if candidate.metadata.get("search_window_days", 365) <= 90 else 2


def select_candidates(buckets: dict[str, list[SourceCandidate]], *, limit: int = 10, as_of=None) -> list[SourceCandidate]:
    unique = group_sources([c for bucket in buckets.values() for c in bucket])
    as_of = as_of or utcnow()
    def rank(c):
        return (_recency_rank(c, as_of), -c.score, -int(c.has_body), c.url)
    chosen = []
    # Reserve representation for each query with eligible results, not a pro/con quota.
    for query_id in buckets:
        if len(chosen) >= limit:
            break
        if any(query_id in c.query_ids for c in chosen):
            continue
        options = sorted([c for c in unique if query_id in c.query_ids and c not in chosen], key=rank)
        if options:
            chosen.append(options[0])
    while len(chosen) < limit:
        remaining = [c for c in unique if c not in chosen]
        if not remaining:
            break
        known_groups = {c.source_group for c in chosen}
        remaining.sort(key=lambda c: (_recency_rank(c, as_of), -(c.score + .1*int(c.has_body) + .05*int(c.source_group not in known_groups)), c.url))
        chosen.append(remaining[0])
    return chosen


def retrieve_evidence(question: QuestionSpec, tasks, data_dir: Path):
    from .schemas import RetrievalTask, RetrievalResult, RetrievalLog, SourceAlias
    from .provenance import save_snapshot, split_passages, select_passages, text_terms
    if not (config.BRAVE_SEARCH_API_KEY or config.TAVILY_API_KEY):
        raise ValueError("在线检索需要 BRAVE_SEARCH_API_KEY 或 TAVILY_API_KEY；可改用导入证据包。")
    if (utcnow() - question.as_of).total_seconds() > 86400:
        raise ValueError("历史问题不能用今天网页冒充截点前快照，请导入历史练习或使用已有冻结材料。")
    if len(tasks) > 3 or len({t.id for t in tasks}) != len(tasks):
        raise ValueError("最多3个且编号不重复的检索任务")
    tasks = tasks or [RetrievalTask(id="R001", query=question.question[:400], purpose="background")]
    def search(task):
        start = time.monotonic()
        result, windows, errors = [], [], []
        for days in (90, 365):
            windows.append(days)
            try:
                rows = _search_one(task.query, days=days, as_of=question.as_of)
                seen = {canonical_source_url(r.get("url", "")) for r in result}
                for row in rows:
                    key = canonical_source_url(row.get("url", ""))
                    if key not in seen:
                        result.append({**row, "search_window_days": days})
                        seen.add(key)
                usable = {r.get("url") for r in result if public_url(str(r.get("url", "")))
                          and (r.get("raw_content") or r.get("content"))}
                if len(usable) >= 3:
                    break
            except Exception as exc:
                detail = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
                errors.append(detail)
                break  # a network/auth failure is not evidence scarcity
        return result, RetrievalLog(task_id=task.id, query=task.query, purpose=task.purpose,
            status="success" if result else "failed" if errors else "empty", result_count=len(result),
            elapsed_seconds=time.monotonic()-start, error="；".join(errors) or None, search_windows_days=windows)
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        responses = list(pool.map(search, tasks))
    buckets, logs, exclusions = {}, [], []
    for task, (results, log) in zip(tasks, responses):
        logs.append(log); buckets[task.id] = []
        for result in results:
            url = result.get("url", "")
            try:
                valid_url = isinstance(url, str) and public_url(url)
            except ValueError:
                valid_url = False
            text = result.get("raw_content") or result.get("content") or ""
            if not valid_url or not isinstance(text, str) or not text.strip():
                exclusions.append({"source": str(url)[:500], "reason": "无效来源地址或空内容"})
                continue
            published = _parse_date(result.get("published_date") or result.get("published_at"))
            updated = _parse_date(result.get("updated_at"))
            event = _parse_date(result.get("event_at"))
            if (published and published > question.as_of) or (updated and updated > question.as_of):
                exclusions.append({"source": url, "reason": "来源发布或更新晚于信息截至时间"})
                continue
            title = str(result.get("title") or url)[:1000]
            overlap = len(text_terms(task.query) & text_terms(title + " " + text[:200_000]))
            raw_score = result.get("score")
            score = float(raw_score) if isinstance(raw_score, (int, float)) and math.isfinite(raw_score) else 0
            if score < .1 and overlap == 0:
                exclusions.append({"source": url, "reason": "未通过基础查询相关性检查"})
                continue
            score = max(min(1.0, score), min(.8, overlap / max(1, len(text_terms(task.query)))))
            metadata = {k: result.get(k) for k in ("published_date", "published_at", "updated_at", "event_at", "score", "page_age", "search_window_days") if result.get(k) is not None}
            metadata["content_hash"] = hashlib.sha256(text.encode()).hexdigest()
            alias = SourceAlias(source_url=url, publisher=urlparse(url).hostname, published_at=published, updated_at=updated, metadata=metadata)
            buckets[task.id].append(SourceCandidate(url=url, title=title, text=text,
                has_body=bool(result.get("raw_content")), score=score, query_ids=[task.id], aliases=[alias],
                metadata=metadata, published_at=published, updated_at=updated, event_at=event))
    candidates = select_candidates(buckets, as_of=question.as_of)
    if config.BRAVE_SEARCH_API_KEY:
        from .source_fetch import fetch_selected_bodies
        pending = [c for c in candidates if not c.has_body]
        fetched = asyncio.run(fetch_selected_bodies([c.url for c in pending]))
        for candidate, (body, metadata) in zip(pending, fetched):
            candidate.metadata["body_fetch"] = metadata
            if body:
                for field in ("published_at", "updated_at"):
                    parsed = _parse_date(metadata.get(field))
                    if parsed:
                        setattr(candidate, field, parsed)
                candidate.metadata["search_excerpt_hash"] = candidate.metadata["content_hash"]
                candidate.text = body.replace("\r\n", "\n").replace("\r", "\n")
                candidate.has_body = True
                candidate.metadata["content_hash"] = hashlib.sha256(candidate.text.encode()).hexdigest()
                final_url = metadata["final_url"]
                candidate.aliases.append(SourceAlias(source_url=final_url, publisher=urlparse(final_url).hostname,
                    metadata={"content_hash": candidate.metadata["content_hash"], "body_fetch": metadata}))
        # Redirects/exact body duplicates may reveal aliases that snippets hid.
        for candidate in candidates:
            candidate.possible_same_source = []
        candidates = group_sources(candidates)
    evidence = []
    background_count = 0
    candidates.sort(key=lambda c: (_recency_rank(c, question.as_of), -c.score, c.url))
    for c in candidates:
        if any(d and d > question.as_of for d in (c.published_at, c.updated_at)):
            exclusions.append({"source": c.url, "reason": "正文发布日期或更新晚于当前预测时间"})
            continue
        age = (question.as_of - c.published_at).days if c.published_at else None
        if age is not None and age > 365:
            exclusions.append({"source": c.url, "reason": "发布时间超过一年，未采用为当前预测依据"})
            continue
        role = "recent" if age is not None and age <= 90 else "background" if age is not None else "date_unknown"
        if role == "background":
            background_count += 1
            if background_count > 2:
                exclusions.append({"source": c.url, "reason": "较旧背景资料限额为两条"})
                continue
        snapshot = save_snapshot(c.text, {"provider": "brave" if config.BRAVE_SEARCH_API_KEY else "tavily", "source_url": c.url, "title": c.title,
            "query_ids": c.query_ids, "query_terms": [t.query for t in tasks if t.id in c.query_ids],
            "source_metadata": c.metadata}, data_dir)
        excerpt = snapshot.text[:12000]
        date_basis = {"retrieved_at": "本次工具实际取得时间"}
        for name in ("published_at", "updated_at", "event_at"):
            if getattr(c, name):
                date_basis[name] = ("页面明确日期标记；非独立核实，不等同事件日期" if c.metadata.get("body_fetch", {}).get(name) else "服务商声明日期；非独立核实") + "；无时区按UTC展示"
        if c.metadata.get("page_age"):
            date_basis["search_page_age"] = "搜索引擎页面年龄（可能是发布或更新）: " + str(c.metadata["page_age"]) + "；仅供排序，不作为事件或发布日期"
        date_basis["recency"] = "优先90天，候选不足3条扩大至365天；未知日期不证明近期"
        cutoff_delay = (snapshot.stored_at - question.as_of).total_seconds()
        live_near_cutoff = 0 <= cutoff_delay <= config.LIVE_CUTOFF_GRACE_SECONDS
        if live_near_cutoff:
            date_basis["cutoff_validation"] = (
                f"实时在线检索在信息截至时间后 {cutoff_delay:.0f} 秒取得；按 "
                f"{config.LIVE_CUTOFF_GRACE_SECONDS} 秒 near-cutoff 窗口接受，仅适用于当前实时预测，不证明历史可用性"
            )
        evidence.append(Evidence(id=f"E{len(evidence)+1:03}", source_url=c.url, title=c.title,
            publisher=urlparse(c.url).hostname, recency_role=role, age_days=age, published_at=c.published_at, updated_at=c.updated_at, event_at=c.event_at,
            retrieved_at=snapshot.stored_at, excerpt=excerpt, content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
            claim="待核查", snapshot_path=snapshot.snapshot_path, snapshot_hash=snapshot.snapshot_hash,
            source_type="secondary" if c.has_body else "snippet_only", source_kind="unknown",
            content_kind="body" if c.has_body else "snippet", content_truncated=snapshot.content_truncated,
            source_group=c.source_group, source_group_basis=c.source_group_basis,
            aliases=c.aliases, query_ids=c.query_ids, possible_same_source=c.possible_same_source,
            date_basis=date_basis, date_status="unknown", availability="live_near_cutoff" if live_near_cutoff else "unverified",
            event_status="planned" if c.event_at and c.event_at > question.as_of else "unknown",
            passages=select_passages(split_passages(snapshot), [question.question, *[t.query for t in tasks]])))
    failures = sum(log.status == "failed" for log in logs)
    status = "failed" if failures == len(tasks) else "partial" if failures or exclusions else "completed"
    return RetrievalResult(evidence=evidence, retrieval_log=logs, exclusions=exclusions, status=status)


def online_search(question: QuestionSpec, data_dir: Path, queries: list[str] | None = None) -> list[Evidence]:
    from .schemas import RetrievalTask
    queries = list(dict.fromkeys(q[:400] for q in (queries or [question.question])[:3] if q.strip()))
    tasks = [RetrievalTask(id=f"R{i+1:03}", query=q, purpose="background") for i, q in enumerate(queries)]
    result = retrieve_evidence(question, tasks, data_dir)
    if result.status == "failed":
        details = "；".join(f"第 {i+1} 条：{log.error}" for i, log in enumerate(result.retrieval_log))
        provider = "Brave" if config.BRAVE_SEARCH_API_KEY else "Tavily"
        raise RuntimeError(f"{provider} 在线检索全部失败（{details}）")
    return result.evidence


def import_evidence(items, question: QuestionSpec, data_dir: Path, *, cutoff_verified: bool = False):
    """Save new imports, never trust a client-supplied old path or timestamp."""
    from .provenance import save_snapshot, split_passages, select_passages
    from .schemas import RetrievalResult
    if len(items) > 20:
        raise ValueError("每个证据包最多20条")
    seen_ids = set()
    evidence, exclusions = [], []
    now = utcnow()
    historical = (now - question.as_of).total_seconds() > 86400
    for item in items:
        if item.id and item.id in seen_ids:
            raise ValueError("重复导入证据编号")
        if item.id:
            seen_ids.add(item.id)
        reason = None
        if item.source_url and not public_url(str(item.source_url)):
            reason = "来源地址不是公开HTTP(S) URL"
        elif (item.published_at and item.published_at > question.as_of) or (item.updated_at and item.updated_at > question.as_of):
            reason = "来源发布或更新晚于信息截至时间"
        elif historical and item.source_type != "exercise":
            reason = "客户端声明不能证明截点前冻结；请使用明确标注回看风险的历史练习"
        if reason:
            exclusions.append({"source": str(item.source_url or item.file_id), "reason": reason})
            continue
        safe = item.model_copy(update={"retrieved_at": now, "snapshot_path": None, "date_status": "unknown"})
        e = normalize_import([safe], question)[0]
        e.id = f"E{len(evidence)+1:03}"
        snapshot = save_snapshot(item.body or item.excerpt, {"provider": "import",
            "source_url": str(item.source_url) if item.source_url else None, "file_id": item.file_id,
            "title": item.title, "declared_snapshot_path": item.snapshot_path,
            "declared_retrieved_at": item.retrieved_at.isoformat() if item.retrieved_at else None}, data_dir)
        e.snapshot_path, e.snapshot_hash = snapshot.snapshot_path, snapshot.snapshot_hash
        e.content_truncated = snapshot.content_truncated
        e.content_kind = "body" if item.body else "imported_excerpt"
        e.source_kind = item.source_kind if item.source_kind_basis else "unknown"
        e.source_kind_basis = "导入者声明（未独立核实）：" + item.source_kind_basis if item.source_kind_basis else ""
        e.source_group = item.source_group or "document:" + hashlib.sha256(str(item.source_url or item.file_id).encode()).hexdigest()[:16]
        e.source_group_basis = ("导入者声明：" + (item.source_group_basis or "未提供分组依据")) if item.source_group else "单独资料，未核实独立性"
        e.availability = ("verified_before_cutoff" if cutoff_verified else
                          "historical_exercise" if historical else "unverified")
        e.event_status = "planned" if item.event_at and item.event_at > question.as_of else item.event_status
        e.date_basis = {"retrieved_at": "本次实际导入时间"}
        if cutoff_verified:
            e.date_basis["cutoff_validation"] = "服务端离线校验通过的冻结评测证据"
        for field in ("published_at", "updated_at", "event_at"):
            if getattr(item, field):
                e.date_basis[field] = "导入者提供，未独立核实"
        e.passages = select_passages(split_passages(snapshot), [question.question])
        evidence.append(e)
    return RetrievalResult(evidence=_deduplicate_imports(evidence), exclusions=exclusions, status="partial" if exclusions else "completed")



def _deduplicate_imports(items):
    from .schemas import SourceAlias
    by_location, by_body, unique = {}, {}, []
    for evidence in items:
        location = canonical_source_url(str(evidence.source_url)) if evidence.source_url else "file:" + evidence.file_id
        alias = SourceAlias(source_url=str(evidence.source_url or evidence.file_id), publisher=evidence.publisher,
            published_at=evidence.published_at, updated_at=evidence.updated_at,
            metadata={"snapshot_hash": evidence.snapshot_hash, "source_group": evidence.source_group,
                      "source_group_basis": evidence.source_group_basis})
        existing = by_location.get(location) or by_body.get(evidence.snapshot_hash)
        if existing is not None:
            if alias not in existing.aliases:
                existing.aliases.append(alias)
            existing.source_group_basis += "；规范化地址或相同正文去重，别名元数据已保留"
            by_location[location] = existing
        else:
            evidence.aliases = [alias]
            unique.append(evidence)
            by_location[location] = by_body[evidence.snapshot_hash] = evidence
    for index, evidence in enumerate(unique, 1):
        evidence.id = f"E{index:03}"
    return unique
