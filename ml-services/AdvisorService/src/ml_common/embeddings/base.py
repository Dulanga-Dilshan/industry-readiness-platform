"""Embedding client contract."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class EmbeddingError(Exception):
    pass


class Embedder(ABC):
    model_id: str = ""
    dim: int | None = None

    @abstractmethod
    async def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        """Return one vector per text. kind is 'document' or 'query'."""

    async def get_dim(self) -> int:
        if self.dim is None:
            self.dim = len((await self.embed(["dimension probe"]))[0])
        return self.dim


async def embed_batched(embedder: Embedder, texts: list[str], batch_size: int = 16, retries: int = 3,
                        base_delay: float = 0.5, kind: str = "document") -> list[list[float]]:
    """Batches with exponential backoff. Raises EmbeddingError after the last retry."""
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(retries + 1):
            try:
                out.extend(await embedder.embed(batch, kind))
                break
            except EmbeddingError:
                if attempt == retries:
                    raise
                await asyncio.sleep(base_delay * (2 ** attempt))
    return out
