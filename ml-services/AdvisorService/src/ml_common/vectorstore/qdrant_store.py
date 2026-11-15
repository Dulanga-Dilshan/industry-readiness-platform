"""Qdrant implementation. Server mode (QDRANT_URL) or local in-process mode for tests."""
from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from .base import Hit, Point, VectorStore

META_SUFFIX = "__meta"


def to_uuid(raw: str) -> str:
    """Qdrant ids must be uuid or int: derive a stable uuid from any string id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def _filter(flt: dict[str, Any] | None):
    if not flt:
        return None
    return models.Filter(must=[models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in flt.items()])


class QdrantVectorStore(VectorStore):
    def __init__(self, url: str | None = None, location: str | None = None):
        self.client = AsyncQdrantClient(url=url) if url else AsyncQdrantClient(location=location or ":memory:")

    async def ensure_collection(self, name, dim):
        if not await self.client.collection_exists(name):
            await self.client.create_collection(
                name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))

    async def upsert(self, name, points: list[Point]):
        if not points:
            return
        await self.client.upsert(name, points=[
            models.PointStruct(id=to_uuid(p.id), vector=p.vector, payload={**p.payload, "_id": p.id}) for p in points])

    async def search(self, name, vector, limit=5, filter=None):
        res = await self.client.query_points(name, query=vector, limit=limit, query_filter=_filter(filter),
                                             with_payload=True)
        return [Hit(h.payload.get("_id", str(h.id)), h.score, h.payload) for h in res.points]

    async def delete(self, name, filter):
        if not await self.client.collection_exists(name):
            return
        await self.client.delete(name, points_selector=models.FilterSelector(filter=_filter(filter)))

    async def scroll(self, name, filter=None, limit=10000):
        if not await self.client.collection_exists(name):
            return []
        out: list[Hit] = []
        offset = None
        while len(out) < limit:
            points, offset = await self.client.scroll(name, scroll_filter=_filter(filter), limit=256,
                                                      offset=offset, with_payload=True)
            out.extend(Hit(p.payload.get("_id", str(p.id)), 0.0, p.payload) for p in points)
            if offset is None:
                break
        return out[:limit]

    async def count(self, name, filter=None):
        if not await self.client.collection_exists(name):
            return 0
        return (await self.client.count(name, count_filter=_filter(filter), exact=True)).count

    async def drop(self, name):
        for n in (name, name + META_SUFFIX):
            if await self.client.collection_exists(n):
                await self.client.delete_collection(n)

    async def exists(self, name):
        return await self.client.collection_exists(name)

    async def get_meta(self, name):
        mname = name + META_SUFFIX
        if not await self.client.collection_exists(mname):
            return None
        pts = await self.client.retrieve(mname, ids=[to_uuid("meta")], with_payload=True)
        return dict(pts[0].payload) if pts else None

    async def set_meta(self, name, meta):
        mname = name + META_SUFFIX
        if not await self.client.collection_exists(mname):
            await self.client.create_collection(
                mname, vectors_config=models.VectorParams(size=1, distance=models.Distance.COSINE))
        await self.client.upsert(mname, points=[models.PointStruct(id=to_uuid("meta"), vector=[1.0], payload=meta)])
