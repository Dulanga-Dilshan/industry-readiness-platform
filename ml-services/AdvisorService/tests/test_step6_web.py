import json

import httpx
import pytest

from advisor.tools.tavily import TavilyProvider
from advisor.tools.web_search import (FakeWebProvider, WebSearchService, html_to_text, is_private_host)
from advisor.tools.wikipedia import WikipediaProvider

R = [("Docker", "https://example.org/docker", "container tool")]


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def svc(**providers):
    clock = Clock()
    return WebSearchService(providers, clock=clock, resolver=lambda h: ["93.184.216.34"]), clock


async def test_order_wikipedia_first_for_definitions():
    wiki, tav = FakeWebProvider("wikipedia", R), FakeWebProvider("tavily", R)
    s, _ = svc(tavily=tav, wikipedia=wiki)
    await s.search("what is docker", "definition")
    assert wiki.calls == 1 and tav.calls == 0
    await s.search("job market trends", "general")
    assert tav.calls == 1


async def test_failure_cools_down_and_next_provider_used():
    bad, good = FakeWebProvider("wikipedia", fail=True), FakeWebProvider("tavily", R)
    s, clock = svc(wikipedia=bad, tavily=good)
    r = await s.search("x y", "definition")
    assert r.status == "ok" and r.data["provider"] == "tavily"
    await s.search("other query", "definition")
    assert bad.calls == 1                                     # still cooling down
    clock.t += 200
    await s.search("third query", "definition")
    assert bad.calls == 2


async def test_all_fail_is_unavailable():
    s, _ = svc(wikipedia=FakeWebProvider("wikipedia", fail=True))
    assert (await s.search("q q", "definition")).status == "unavailable"


async def test_cache_24h():
    p = FakeWebProvider("wikipedia", R)
    s, clock = svc(wikipedia=p)
    await s.search("what is docker", "definition")
    await s.search("What is Docker", "definition")
    assert p.calls == 1
    clock.t += 25 * 3600
    await s.search("what is docker", "definition")
    assert p.calls == 2


async def test_profile_data_never_in_query():
    p = FakeWebProvider("wikipedia", R)
    s, _ = svc(wikipedia=p)
    profile = {"goals": ["become a security engineer at Acme Bank"], "user_id": "student-12345"}
    r = await s.search("jobs for become a security engineer at acme bank", "general", profile)
    assert r.status == "error" and p.calls == 0
    assert (await s.search("contact me at kamal@example.com", "general")).status == "error"


async def test_fetch_only_returned_urls_and_blocks_private():
    s, _ = svc(wikipedia=FakeWebProvider("wikipedia", [("T", "http://127.0.0.1/admin", "x"), ("U", "https://example.org/p", "y")]))
    await s.search("q q", "definition")
    assert (await s.fetch("https://never-returned.example.com")).status == "error"
    r = await s.fetch("http://127.0.0.1/admin")
    assert r.status == "error" and "not allowed" in r.reason


def test_private_host_detection():
    r = lambda h: ["10.0.0.5"]
    assert is_private_host("localhost") and is_private_host("169.254.169.254") and is_private_host("192.168.1.1")
    assert is_private_host("internal.example.com", r) and is_private_host("db.internal")
    assert not is_private_host("example.org", lambda h: ["93.184.216.34"])


async def test_fetch_extracts_text_and_blocks_redirect_to_private():
    def handler(request):
        if request.url.path == "/redir":
            return httpx.Response(302, headers={"location": "http://10.0.0.1/secret"})
        return httpx.Response(200, text="<html><head><title>x</title></head><body><script>bad()</script><p>Hello world</p></body></html>")
    s = WebSearchService({"w": FakeWebProvider("w", [("A", "https://example.org/page", ""), ("B", "https://example.org/redir", "")])},
                         transport=httpx.MockTransport(handler), resolver=lambda h: ["93.184.216.34"] if "example" in h else ["10.0.0.1"])
    await s.search("q q")
    ok = await s.fetch("https://example.org/page")
    assert ok.data["text"] == "Hello world"
    bad = await s.fetch("https://example.org/redir")
    assert bad.status == "error" and "not allowed" in bad.reason


def test_html_to_text_skips_scripts():
    assert html_to_text("<style>a{}</style><b>Hi</b><script>x</script> there") == "Hi there"


async def test_wikipedia_provider_parses_mediawiki_response():
    def handler(request):
        assert request.headers["user-agent"].startswith("IRP-Advisor")
        pages = {"1": {"index": 2, "title": "B", "fullurl": "https://en.wikipedia.org/wiki/B", "extract": "bee"},
                 "2": {"index": 1, "title": "A", "fullurl": "https://en.wikipedia.org/wiki/A", "extract": "ay"}}
        return httpx.Response(200, json={"query": {"pages": pages}})
    res = await WikipediaProvider(httpx.MockTransport(handler)).search("a", "definition")
    assert [r.title for r in res] == ["A", "B"] and res[0].url.endswith("/A")


async def test_tavily_only_with_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    t = TavilyProvider()
    assert not t.available()
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    assert t.available()

    def handler(request):
        assert json.loads(request.content)["query"] == "q"
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example", "content": "c"}]})
    assert (await TavilyProvider(transport=httpx.MockTransport(handler)).search("q", "general"))[0].url == "https://t.example"


async def test_unavailable_tavily_skipped_without_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    s, _ = svc(tavily=TavilyProvider())
    assert (await s.search("q q", "general")).status == "unavailable"
