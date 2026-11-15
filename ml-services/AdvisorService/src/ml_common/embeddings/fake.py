"""Deterministic bag-of-words hashing embeddings (similar texts get similar vectors)."""
from __future__ import annotations

import hashlib
import math
import re

from .base import Embedder, EmbeddingError


class FakeEmbedder(Embedder):
    def __init__(self, dim: int = 512, model_id: str = "fake-embed-v1"):
        self.dim = dim
        self.model_id = model_id
        self.calls = 0
        self.fail_next = 0

    async def embed(self, texts, kind="document"):
        self.calls += 1
        if self.fail_next:
            self.fail_next -= 1
            raise EmbeddingError("scripted embedding failure")
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9+#.]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]
