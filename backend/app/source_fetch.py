"""Bounded, public-only page reads for selected search results (no browser bypass)."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import ipaddress
import queue
import re
import socket
import threading
from urllib.parse import urljoin, urlsplit
import zlib

import httpx

PAGE_TIMEOUT_SECONDS = 6.0
BATCH_TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_CONCURRENCY = 3
MAX_SOURCES = 10


class BodyUnavailable(ValueError):
    """A safe, non-sensitive fallback reason for an otherwise usable snippet."""


def _url_parts(url: str):
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError):
        raise BodyUnavailable("invalid_url") from None
    if (parts.scheme not in {"http", "https"} or not host or parts.username or parts.password
            or port not in {80, 443} or "%" in host or "\\" in url
            or any(ord(c) < 32 or ord(c) == 127 for c in url)
            or host in {"localhost", "localhost.localdomain"}
            or host.endswith((".local", ".internal", ".localhost"))):
        raise BodyUnavailable("non_public_url")
    return parts, host, port


def _global_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
        return ip.is_global and not ip.is_multicast and not ip.is_unspecified
    except ValueError:
        return False


async def _public_addresses(host: str, port: int) -> list[str]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # libc DNS has no portable timeout. A daemon resolver never holds up the
        # batch/event-loop shutdown; at most MAX_SOURCES * redirects are started.
        answer = queue.Queue(maxsize=1)
        def resolve():
            try:
                answer.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except OSError:
                answer.put(None)
        threading.Thread(target=resolve, daemon=True).start()
        while answer.empty():
            await asyncio.sleep(.01)
        records = answer.get_nowait()
        if not records:
            raise BodyUnavailable("dns_failed")
        addresses = list(dict.fromkeys(row[4][0] for row in records))
    else:
        addresses = [host]
    # Reject mixed public/private answers, including mapped IPv6 and metadata IPs.
    if not addresses or not all(_global_ip(ip) for ip in addresses):
        raise BodyUnavailable("non_public_address")
    return sorted(addresses, key=lambda ip: ":" in ip)


class _PageText(HTMLParser):
    _skip = {"head", "script", "style", "noscript", "svg", "canvas", "nav", "header",
             "footer", "form", "aside", "dialog", "template"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}
    _blocks = {"p", "div", "article", "main", "section", "li", "ul", "ol", "table", "tr",
               "h1", "h2", "h3", "h4", "blockquote", "pre", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.all_text, self.main_text, self.title = [], [], []
        self.link_chars = [0, 0]
        self.gated = False
        self.in_title = False

    def _break(self):
        self.all_text.append("\n")
        self.main_text.append("\n")

    def handle_starttag(self, tag, attrs):
        # HTML permits attributes without an explicit value (e.g. <main class>).
        # HTMLParser returns None for these, including ordinary string attributes.
        attrs = {name: value if value is not None else "" for name, value in attrs}
        marker = (attrs.get("id", "") + " " + attrs.get("class", "")).lower()
        if re.search(r"(?:^|[\s_-])(?:paywall|login-wall|cookie-wall|subscribe-wall)(?:$|[\s_-])", marker):
            self.gated = True
        if tag == "title":
            self.in_title = True
        if tag in self._blocks:
            self._break()
        parent = self.stack[-1] if self.stack else ("", False, False, False)
        ignored = (parent[1] or tag in self._skip or "hidden" in attrs
                   or attrs.get("aria-hidden", "").lower() == "true"
                   or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style", ""), re.I))
                   or bool(re.search(r"cookie[-_ ]?(?:banner|consent)|share[-_ ]buttons", marker)))
        preferred = (parent[2] or tag in {"article", "main"} or attrs.get("role") == "main"
                     or bool(re.search(r"(?:article[-_]?body|article[-_]content|post[-_]content|entry[-_]content|markdown-body|wp_articlecontent|trs_ueditor|trs_editor)", marker)))
        if tag not in self._void:
            self.stack.append((tag, ignored, preferred, parent[3] or tag == "a"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self._blocks:
            self._break()
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        _, ignored, preferred, link = self.stack[-1] if self.stack else ("", False, False, False)
        if ignored:
            return
        self.all_text.append(data)
        if preferred:
            self.main_text.append(data)
        if link:
            self.link_chars[0] += len(data.strip())
            if preferred:
                self.link_chars[1] += len(data.strip())

    @staticmethod
    def clean(chunks):
        return "\n".join(line for raw in "".join(chunks).splitlines()
                         if (line := re.sub(r"[\t \xa0]+", " ", raw).strip()))

    def extract(self):
        main, all_text = self.clean(self.main_text), self.clean(self.all_text)
        text, selected = (main, 1) if len(main) >= 200 else (all_text, 0)
        title = re.sub(r"\s+", " ", "".join(self.title)).strip()
        if (self.gated or re.search(r"^(?:just a moment|attention required|access denied|sign in|log in|登录|安全验证|验证码|访问验证)\b", title, re.I)
                or (len(text) < 1500 and re.search(
                    r"verify (?:that )?you are human|checking your browser|enable javascript (?:and cookies|to continue)|"
                    r"subscribe to (?:continue|read)|sign in to (?:continue|read)|log in to (?:continue|read)|"
                    r"accept cookies to (?:continue|read)|安全验证|请输入验证码|登录后(?:查看|阅读)|订阅后(?:查看|阅读)", text, re.I))):
            raise BodyUnavailable("access_gate")
        if len(text) < 200 or self.link_chars[selected] > len(text) * .55:
            raise BodyUnavailable("insufficient_body")
        if not any(len(line) >= 100 for line in text.splitlines()):
            raise BodyUnavailable("navigation_only")
        return text, "article_or_main" if selected else "visible_page_text"


async def _read_body(url: str) -> tuple[str, dict]:
    current, redirects = url, []
    for hop in range(MAX_REDIRECTS + 1):
        _, host, port = _url_parts(current)
        addresses = await _public_addresses(host, port)
        original = httpx.URL(current)
        # Connect to the validated IP, not another DNS answer. Host and TLS SNI/
        # certificate validation still use the original name (httpcore extension).
        pinned = original.copy_with(host=addresses[0], fragment=None)
        headers = {"Host": original.netloc.decode("ascii"), "Accept": "text/html,text/plain;q=0.9",
                   "Accept-Encoding": "identity", "User-Agent": "ForecastLab-SourceReader/1.0"}
        # No ambient/configured proxy: proxy-side DNS would invalidate IP pinning.
        # A blocked direct connection is a labelled snippet fallback, never bypass.
        async with httpx.AsyncClient(timeout=3.0, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if hop == MAX_REDIRECTS or not response.headers.get("location"):
                        raise BodyUnavailable("redirect_limit")
                    destination = urljoin(current, response.headers["location"])
                    _url_parts(destination)
                    if urlsplit(current).scheme == "https" and urlsplit(destination).scheme != "https":
                        raise BodyUnavailable("https_downgrade")
                    redirects.append({"url": current, "status": response.status_code})
                    current = destination
                    continue
                if response.status_code != 200:
                    raise BodyUnavailable(f"http_{response.status_code}")
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise BodyUnavailable("unsupported_content_type")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                    raise BodyUnavailable("response_too_large")
                encoding = response.headers.get("content-encoding", "identity").lower().strip()
                if encoding not in {"identity", "gzip", ""}:
                    raise BodyUnavailable("unsupported_encoding")
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
                body, wire_bytes = bytearray(), 0
                async for chunk in response.aiter_raw():
                    wire_bytes += len(chunk)
                    if wire_bytes > MAX_RESPONSE_BYTES:
                        raise BodyUnavailable("response_too_large")
                    if decoder:
                        chunk = decoder.decompress(chunk, MAX_RESPONSE_BYTES + 1 - len(body))
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise BodyUnavailable("response_too_large")
                if decoder and not decoder.eof:
                    raise BodyUnavailable("incomplete_response")
                charset = response.charset_encoding
                if not charset:
                    meta = re.search(br"charset\s*=\s*[\"']?\s*([a-zA-Z0-9_-]+)", body[:8192])
                    charset = meta.group(1).decode("ascii") if meta else "utf-8"
                try:
                    decoded = body.decode(charset, errors="replace")
                except LookupError:
                    decoded = body.decode("utf-8", errors="replace")
                if mime == "text/plain":
                    text, extraction = decoded.strip(), "plain_text"
                    if re.search(r"verify (?:that )?you are human|sign in to (?:continue|read)|subscribe to (?:continue|read)|请输入验证码", text[:1500], re.I):
                        raise BodyUnavailable("access_gate")
                    if len(text) < 200:
                        raise BodyUnavailable("insufficient_body")
                else:
                    parser = _PageText()
                    parser.feed(decoded)
                    parser.close()
                    text, extraction = parser.extract()
                return text, {"final_url": current, "redirects": redirects,
                              "http_status": 200, "content_type": mime,
                              "extraction": extraction, "response_bytes": len(body),
                              "transport": "direct_pinned_public_ip"}
    raise BodyUnavailable("redirect_limit")


async def fetch_selected_bodies(urls: list[str]) -> list[tuple[str | None, dict]]:
    """Selected-result order is stable; failed reads keep their original snippet."""
    from .schemas import utcnow
    if len(urls) > MAX_SOURCES:
        raise ValueError("Too many selected source bodies")
    results = [(None, {"status": "unavailable", "reason": "batch_timeout"}) for _ in urls]
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async def fetch(index, url):
        async with semaphore:
            attempted_at = utcnow().isoformat()
            try:
                async with asyncio.timeout(PAGE_TIMEOUT_SECONDS):
                    text, metadata = await _read_body(url)
                results[index] = (text, {"status": "success", **metadata, "fetched_at": utcnow().isoformat()})
            except (TimeoutError, httpx.TimeoutException):
                results[index] = (None, {"status": "unavailable", "reason": "timeout"})
            except BodyUnavailable as exc:
                results[index] = (None, {"status": "unavailable", "reason": str(exc)})
            except (httpx.HTTPError, OSError, ValueError, zlib.error):
                results[index] = (None, {"status": "unavailable", "reason": "fetch_failed"})
            finally:
                results[index][1]["attempted_at"] = attempted_at
    try:
        async with asyncio.timeout(BATCH_TIMEOUT_SECONDS):
            await asyncio.gather(*(fetch(i, url) for i, url in enumerate(urls)))
    except TimeoutError:
        pass
    for _, metadata in results:
        metadata.setdefault("attempted_at", utcnow().isoformat())
    return results
