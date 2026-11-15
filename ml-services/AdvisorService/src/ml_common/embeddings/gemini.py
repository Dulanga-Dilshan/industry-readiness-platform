"""Gemini embeddings. gemini-embedding-2 merges a list of inputs into ONE vector, so each text
is sent in its own embedContent call (bounded concurrency)."""
from __future__ import annotations

import asyncio
import os

import httpx

from .base import Embedder, EmbeddingError

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiEmbedder(Embedder):
    def __init__(self, model: str = "gemini-embedding-2", api_key_env: str = "GEMINI_API_KEY",
                 concurrency: int = 4, transport: httpx.AsyncBaseTransport | None = None,
                 doc_prefix: str = "", query_prefix: str = ""):
        self.model = model
        self.model_id = f"gemini:{model}"
        self.api_key_env = api_key_env
        self.sem = asyncio.Semaphore(concurrency)
        self._transport = transport
        self.prefix = {"document": doc_prefix, "query": query_prefix}   # task instructions, optional

    async def _one(self, client: httpx.AsyncClient, text: str, kind: str) -> list[float]:
        body = {"model": f"models/{self.model}", "content": {"parts": [{"text": self.prefix.get(kind, "") + text}]}}
        async with self.sem:
            try:
                r = await client.post(f"{BASE}/models/{self.model}:embedContent", json=body,
                                      headers={"x-goog-api-key": os.environ.get(self.api_key_env, "")})
            except httpx.HTTPError as e:
                raise EmbeddingError(f"gemini not reachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise EmbeddingError(f"gemini embed {r.status_code}")
        values = (r.json().get("embedding") or {}).get("values")
        if not values:
            raise EmbeddingError("gemini returned no vector")
        return values

    async def embed(self, texts, kind="document"):
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), transport=self._transport) as c:
            vecs = await asyncio.gather(*[self._one(c, t, kind) for t in texts])
        self.dim = len(vecs[0]) if vecs else self.dim
        return list(vecs)
