"""Web tools: provider interface, ordered providers with cooldowns, cache, safe fetch."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from advisor.safety.redaction import query_leaks_profile

from .envelope import ToolResult

MAX_FETCH_BYTES = 200_000
MAX_TEXT_CHARS = 4000
MAX_REDIRECTS = 3


class WebProviderError(Exception):
    pass


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str
    provider: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "provider": self.provider}


class WebSearchProvider(ABC):
    name = ""
    kinds = ("definition", "general")

    def available(self) -> bool:
        return True

    @abstractmethod
    async def search(self, query: str, kind: str) -> list[WebResult]: ...


class FakeWebProvider(WebSearchProvider):
    def __init__(self, name="fake", results=None, fail=False):
        self.name, self.results, self.fail, self.calls = name, results or [], fail, 0

    async def search(self, query, kind):
        self.calls += 1
        if self.fail:
            raise WebProviderError("scripted failure")
        return [WebResult(t, u, s, self.name) for t, u, s in self.results]


def is_private_host(host: str, resolver: Callable[[str], list[str]] | None = None) -> bool:
    host = host.strip("[]").lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        addrs = [host]
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addrs = (resolver or _resolve)(host)
        except OSError:
            return True        # cannot resolve: do not fetch
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


def _resolve(host: str) -> list[str]:
    return list({i[4][0] for i in socket.getaddrinfo(host, None)})


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "head"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "head") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return " ".join(p.parts)


class WebSearchService:
    def __init__(self, providers: dict[str, WebSearchProvider], order: Callable[[], list[str]] | None = None,
                 cache_hours: Callable[[], float] | float = 24, clock: Callable[[], float] = time.time,
                 cooldown_s: float = 120.0, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Callable[[str], list[str]] | None = None):
        self.providers = providers
        self._order = order or (lambda: list(providers))
        self._cache_hours = cache_hours
        self.clock, self.cooldown_s, self.transport, self.resolver = clock, cooldown_s, transport, resolver
        self.cache: dict[tuple, tuple[float, list[WebResult], str]] = {}
        self.cooldown_until: dict[str, float] = {}
        self.counters: dict[str, int] = {}
        self.seen_urls: set[str] = set()

    def _hours(self) -> float:
        return self._cache_hours() if callable(self._cache_hours) else self._cache_hours

    def _order_for(self, kind: str) -> list[str]:
        names = [n for n in self._order() if n in self.providers]
        if kind == "definition":
            names.sort(key=lambda n: 0 if n == "wikipedia" else 1)     # stable: wikipedia first for definitions
        else:
            names.sort(key=lambda n: 1 if n == "wikipedia" else 0)
        return names

    async def search(self, query: str, kind: str = "general", profile: dict | None = None) -> ToolResult:
        query = " ".join(query.split())[:200]
        if not query:
            return ToolResult.error("empty query")
        if query_leaks_profile(query, profile):
            return ToolResult.error("query contains personal data and was not sent")
        key = (kind, query.lower())
        hit = self.cache.get(key)
        if hit and self.clock() - hit[0] < self._hours() * 3600:
            return self._result(hit[1], hit[2])
        tried = 0
        for name in self._order_for(kind):
            p = self.providers[name]
            if not p.available() or self.cooldown_until.get(name, 0) > self.clock():
                continue
            tried += 1
            try:
                results = await p.search(query, kind)
            except (WebProviderError, httpx.HTTPError):
                self.cooldown_until[name] = self.clock() + self.cooldown_s
                continue
            self.counters[name] = self.counters.get(name, 0) + 1
            for r in results:
                self.seen_urls.add(r.url)
            self.cache[key] = (self.clock(), results, name)
            return self._result(results, name)
        return ToolResult.unavailable("web search is unavailable right now")

    @staticmethod
    def _result(results: list[WebResult], provider: str) -> ToolResult:
        if not results:
            return ToolResult.empty("web search found nothing")
        return ToolResult.ok({"provider": provider, "results": [r.to_dict() for r in results]})

    async def fetch(self, url: str) -> ToolResult:
        if url not in self.seen_urls:
            return ToolResult.error("only URLs returned by web_search can be fetched")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), transport=self.transport,
                                         follow_redirects=False, headers={"User-Agent": "IRP-Advisor/0.1"}) as c:
                current = url
                for _ in range(MAX_REDIRECTS + 1):
                    u = urlparse(current)
                    if u.scheme not in ("http", "https") or not u.hostname:
                        return ToolResult.error("only http(s) URLs are allowed")
                    if await asyncio.to_thread(is_private_host, u.hostname, self.resolver):
                        return ToolResult.error("this address is not allowed")
                    async with c.stream("GET", current) as r:
                        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                            current = urljoin(current, r.headers["location"])
                            continue
                        if r.status_code >= 400:
                            return ToolResult.unavailable(f"page returned {r.status_code}")
                        body = b""
                        async for chunk in r.aiter_bytes():
                            body += chunk
                            if len(body) > MAX_FETCH_BYTES:
                                break
                        text = html_to_text(body[:MAX_FETCH_BYTES].decode("utf-8", "replace"))
                        return ToolResult.ok({"url": url, "text": text[:MAX_TEXT_CHARS]})
                return ToolResult.error("too many redirects")
        except httpx.HTTPError:
            return ToolResult.unavailable("page could not be fetched")
