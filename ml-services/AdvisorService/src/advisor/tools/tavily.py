"""Optional Tavily search. Enabled only when its API key is present in the environment."""
from __future__ import annotations

import os

import httpx

from .web_search import WebProviderError, WebResult, WebSearchProvider


class TavilyProvider(WebSearchProvider):
    name = "tavily"

    def __init__(self, api_key_env: str = "TAVILY_API_KEY", transport: httpx.AsyncBaseTransport | None = None):
        self.api_key_env, self.transport = api_key_env, transport

    def available(self) -> bool:
        return bool(os.environ.get(self.api_key_env))

    async def search(self, query, kind):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0), transport=self.transport) as c:
                r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": 4,
                                                                         "search_depth": "basic"},
                                 headers={"Authorization": f"Bearer {os.environ.get(self.api_key_env, '')}"})
        except httpx.HTTPError as e:
            raise WebProviderError(str(e)) from e
        if r.status_code >= 400:
            raise WebProviderError(f"tavily {r.status_code}")
        return [WebResult(x.get("title", ""), x.get("url", ""), (x.get("content") or "")[:500], self.name)
                for x in r.json().get("results", []) if x.get("url")]
