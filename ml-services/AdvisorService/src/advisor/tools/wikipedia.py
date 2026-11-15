"""Wikipedia via the official MediaWiki API (no key). Answers must link back to the page."""
from __future__ import annotations

import httpx

from .web_search import WebProviderError, WebResult, WebSearchProvider

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "IRP-Advisor/0.1 (university research project; contact: set-your-email)"


class WikipediaProvider(WebSearchProvider):
    name = "wikipedia"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, limit: int = 3):
        self.transport, self.limit = transport, limit

    async def search(self, query, kind):
        params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": query,
                  "gsrlimit": self.limit, "prop": "extracts|info", "exintro": 1, "explaintext": 1,
                  "exsentences": 3, "exlimit": self.limit, "inprop": "url"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), transport=self.transport,
                                         headers={"User-Agent": USER_AGENT}) as c:
                r = await c.get(API, params=params)
        except httpx.HTTPError as e:
            raise WebProviderError(str(e)) from e
        if r.status_code >= 400:
            raise WebProviderError(f"wikipedia {r.status_code}")
        pages = (r.json().get("query") or {}).get("pages") or {}
        ordered = sorted(pages.values(), key=lambda p: p.get("index", 0))
        return [WebResult(p.get("title", ""), p.get("fullurl", ""), (p.get("extract") or "")[:500], self.name)
                for p in ordered if p.get("fullurl")]
