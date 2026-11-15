"""Optional semantic answer cache. Only for non-personal, non-volatile questions. Off by default."""
from __future__ import annotations

import math
import time
from typing import Callable

from ml_common.embeddings.base import Embedder


def _cos(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)) or 1.0, math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class SemanticCache:
    def __init__(self, embedder: Embedder, threshold: Callable[[], float] | float = 0.95,
                 ttl: Callable[[], float] | float = 86400, clock: Callable[[], float] = time.time, max_items: int = 500):
        self.embedder, self._thr, self._ttl, self.clock, self.max_items = embedder, threshold, ttl, clock, max_items
        self.items: list[dict] = []
        self.hits = self.misses = 0

    def _v(self, x):
        return x() if callable(x) else x

    async def get(self, question: str, scope: str) -> dict | None:
        vec = (await self.embedder.embed([question], "query"))[0]
        now = self.clock()
        best, best_s = None, 0.0
        for it in self.items:
            if it["scope"] != scope or now - it["ts"] > self._v(self._ttl):
                continue
            s = _cos(vec, it["vec"])
            if s > best_s:
                best, best_s = it, s
        if best and best_s >= self._v(self._thr):
            self.hits += 1
            return best
        self.misses += 1
        return None

    async def put(self, question: str, scope: str, answer: str, sources: list[dict]) -> None:
        vec = (await self.embedder.embed([question], "query"))[0]
        self.items.append({"vec": vec, "scope": scope, "answer": answer, "sources": sources, "ts": self.clock()})
        del self.items[:-self.max_items]

    def clear(self) -> None:
        self.items.clear()
