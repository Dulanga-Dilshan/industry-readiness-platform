"""In-memory VectorStore used by tests (cosine similarity)."""
from __future__ import annotations

import math
from typing import Any

from .base import Hit, Point, VectorStore


def _match(payload: dict, flt: dict | None) -> bool:
    return not flt or all(payload.get(k) == v for k, v in flt.items())


def _cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class MemoryVectorStore(VectorStore):
    def __init__(self):
        self.cols: dict[str, dict[str, Point]] = {}
        self.dims: dict[str, int] = {}
        self.meta: dict[str, dict] = {}

    async def ensure_collection(self, name, dim):
        if name in self.dims and self.dims[name] != dim:
            raise ValueError(f"collection {name} has dim {self.dims[name]}, not {dim}")
        self.cols.setdefault(name, {})
        self.dims[name] = dim

    async def upsert(self, name, points):
        col = self.cols[name]
        for p in points:
            if len(p.vector) != self.dims[name]:
                raise ValueError("wrong vector size")
            col[p.id] = p

    async def search(self, name, vector, limit=5, filter=None):
        hits = [Hit(p.id, _cos(vector, p.vector), p.payload) for p in self.cols.get(name, {}).values()
                if _match(p.payload, filter)]
        return sorted(hits, key=lambda h: -h.score)[:limit]

    async def delete(self, name, filter):
        col = self.cols.get(name, {})
        for pid in [pid for pid, p in col.items() if _match(p.payload, filter)]:
            del col[pid]

    async def scroll(self, name, filter=None, limit=10000):
        return [Hit(p.id, 0.0, p.payload) for p in list(self.cols.get(name, {}).values())[:limit]
                if _match(p.payload, filter)]

    async def count(self, name, filter=None):
        return sum(1 for p in self.cols.get(name, {}).values() if _match(p.payload, filter))

    async def drop(self, name):
        self.cols.pop(name, None)
        self.dims.pop(name, None)
        self.meta.pop(name, None)

    async def exists(self, name):
        return name in self.cols

    async def get_meta(self, name):
        return self.meta.get(name)

    async def set_meta(self, name, meta):
        self.meta[name] = dict(meta)
