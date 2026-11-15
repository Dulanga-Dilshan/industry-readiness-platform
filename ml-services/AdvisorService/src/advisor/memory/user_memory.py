"""Facts and summaries indexed per user. Every query is forced through a user_id filter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ml_common.embeddings.base import Embedder, embed_batched
from ml_common.vectorstore.base import Point, VectorStore

COLLECTION = "user_memory"


class UserMemory:
    def __init__(self, store: VectorStore, embedder: Embedder):
        self.store, self.embedder = store, embedder

    async def sync(self, profile: dict, retention_days: int = 180) -> int:
        """Replace all of one user's points with their current facts and summaries."""
        uid = profile["user_id"]
        await self.store.ensure_collection(COLLECTION, await self.embedder.get_dim())
        await self.store.delete(COLLECTION, {"user_id": uid})
        if profile.get("consent", {}).get("store_memory") is False:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        items = [("fact", f["id"], f["text"]) for f in profile.get("facts", [])]
        for s in profile.get("summaries", []):
            try:
                ts = datetime.fromisoformat(str(s.get("timestamp", "")).replace("Z", "+00:00"))
                if ts.tzinfo and ts < cutoff:
                    continue                       # expired
            except ValueError:
                pass
            items.append(("summary", s["id"], s["text"]))
        if not items:
            return 0
        vecs = await embed_batched(self.embedder, [t for _, _, t in items])
        await self.store.upsert(COLLECTION, [Point(f"{uid}:{kind}:{i}", v, {"user_id": uid, "kind": kind, "text": t})
                                             for (kind, i, t), v in zip(items, vecs)])
        return len(items)

    async def search(self, user_id: str, query: str, limit: int = 3) -> list[dict]:
        if not user_id:
            raise ValueError("user_id is required")
        if not await self.store.exists(COLLECTION):
            return []
        qv = (await self.embedder.embed([query], "query"))[0]
        hits = await self.store.search(COLLECTION, qv, limit, {"user_id": user_id})
        return [{"kind": h.payload["kind"], "text": h.payload["text"], "score": round(h.score, 3)} for h in hits]

    async def delete_user(self, user_id: str) -> None:
        await self.store.delete(COLLECTION, {"user_id": user_id})
