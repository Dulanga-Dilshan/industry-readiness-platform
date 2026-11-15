"""Ollama embeddings via POST /api/embed (input: string or list, returns `embeddings`)."""
from __future__ import annotations

import httpx

from .base import Embedder, EmbeddingError


class OllamaEmbedder(Embedder):
    def __init__(self, base_url: str, model: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_id = f"ollama:{model}"
        self._transport = transport

    async def embed(self, texts, kind="document"):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0), transport=self._transport) as c:
                r = await c.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts})
        except httpx.HTTPError as e:
            raise EmbeddingError(f"ollama not reachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise EmbeddingError(f"ollama embed {r.status_code}: {r.text[:200]}")
        vecs = r.json().get("embeddings") or []
        if len(vecs) != len(texts):
            raise EmbeddingError(f"expected {len(texts)} vectors, got {len(vecs)}")
        self.dim = len(vecs[0])
        return vecs
