"""Knowledge index: first-start build, incremental update by file hash, hybrid search."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

from ml_common.embeddings.base import Embedder, EmbeddingError, embed_batched
from ml_common.vectorstore.base import Point, VectorStore

from .chunker import Chunk, chunk_file
from .keyword_index import KeywordIndex
from .loader import load_kb
from .validator import validate_kb

log = logging.getLogger("advisor.knowledge")
COLLECTION = "kb"
RRF_K = 60


class IndexMismatchError(Exception):
    """Index was built with another embedding model; queries are refused."""


@dataclass
class ChunkHit:
    id: str
    score: float             # fused rank score
    vector_score: float      # cosine from the vector search (0 if keyword-only)
    keyword_score: float
    payload: dict


class KnowledgeIndex:
    def __init__(self, store: VectorStore, embedder: Embedder, kb_dir: str, collection: str = COLLECTION,
                 min_vector_score: float = 0.3, batch_size: int = 16):
        self.store, self.embedder, self.kb_dir = store, embedder, kb_dir
        self.collection = collection
        self.min_vector_score = min_vector_score
        self.batch_size = batch_size
        self.state = "building"        # building | ready | failed
        self.error = ""
        self.chunks: dict[str, Chunk] = {}
        self.keyword = KeywordIndex()
        self.content_hash = ""
        self._meta: dict | None = None
        self._lock = asyncio.Lock()

    # ---- build ------------------------------------------------------------
    async def ensure(self, force: bool = False) -> dict:
        async with self._lock:
            self.state, self.error = "building", ""
            try:
                return await self._ensure(force)
            except EmbeddingError as e:
                self.state, self.error = "failed", str(e)
                log.error("knowledge index build failed: %s", e)
                return {"state": "failed", "error": self.error}
            except Exception as e:   # never crash the service because of the index
                self.state, self.error = "failed", f"{type(e).__name__}: {e}"
                log.exception("knowledge index build failed")
                return {"state": "failed", "error": self.error}

    async def _ensure(self, force: bool) -> dict:
        files = [f for f in load_kb(self.kb_dir) if f.approved and not f.problems]
        self.chunks = {}
        for f in files:
            for c in chunk_file(f):
                self.chunks[c.id] = c
        self.keyword.build([(c.id, c.text) for c in self.chunks.values()])
        self.content_hash = hashlib.sha256("".join(sorted(f"{f.rel}:{f.hash}" for f in files)).encode()).hexdigest()[:16]

        dim = await self.embedder.get_dim()
        meta = await self.store.get_meta(self.collection)
        if force or (meta and (meta.get("model_id") != self.embedder.model_id or meta.get("dim") != dim)):
            log.info("rebuilding knowledge index (force=%s, model changed=%s)", force, bool(meta))
            await self.store.drop(self.collection)
            meta = None
        await self.store.ensure_collection(self.collection, dim)
        base_meta = {"model_id": self.embedder.model_id, "dim": dim, "built_at": time.time(),
                     "content_hash": self.content_hash, "status": "building"}
        await self.store.set_meta(self.collection, base_meta)

        existing: dict[str, str] = {}
        for h in await self.store.scroll(self.collection):
            existing[h.payload.get("file", "")] = h.payload.get("file_hash", "")
        current = {f.rel: f.hash for f in files}
        for rel in set(existing) - set(current):                       # removed files
            await self.store.delete(self.collection, {"file": rel})
        embedded = 0
        for f in files:
            if existing.get(f.rel) == f.hash:
                continue
            await self.store.delete(self.collection, {"file": f.rel})   # changed file
            chunks = chunk_file(f)
            if not chunks:
                continue
            vecs = await embed_batched(self.embedder, [c.text for c in chunks], self.batch_size)
            await self.store.upsert(self.collection, [Point(c.id, v, c.payload) for c, v in zip(chunks, vecs)])
            embedded += len(chunks)      # file committed atomically: an interrupted build resumes here
        self._meta = {**base_meta, "status": "ready"}
        await self.store.set_meta(self.collection, self._meta)
        self.state = "ready"
        return {"state": "ready", "chunks": len(self.chunks), "embedded_now": embedded}

    # ---- search -----------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.state == "ready"

    async def search(self, query: str, cluster: str | None = None, type: str | None = None,
                     limit: int = 5) -> list[ChunkHit]:
        if not self.ready:
            raise RuntimeError("knowledge index is not ready")
        if self._meta and self._meta.get("model_id") != self.embedder.model_id:
            raise IndexMismatchError(
                f"index was built with {self._meta.get('model_id')} but the active model is {self.embedder.model_id}")

        def ok(p: dict) -> bool:
            if type and p.get("type") != type:
                return False
            if cluster and p.get("cluster") not in (cluster, "all", "core"):
                return False
            return True

        qv = (await self.embedder.embed([query], "query"))[0]
        vec_hits = [h for h in await self.store.search(self.collection, qv, limit * 4) if ok(h.payload)]
        kw_hits = [(i, s) for i, s in self.keyword.search(query, limit * 4)
                   if i in self.chunks and ok(self.chunks[i].payload)]
        fused: dict[str, ChunkHit] = {}
        for rank, h in enumerate(vec_hits):
            fused[h.id] = ChunkHit(h.id, 1 / (RRF_K + rank + 1), h.score, 0.0, h.payload)
        for rank, (cid, s) in enumerate(kw_hits):
            if cid in fused:
                fused[cid].score += 1 / (RRF_K + rank + 1)
                fused[cid].keyword_score = s
            else:
                fused[cid] = ChunkHit(cid, 1 / (RRF_K + rank + 1), 0.0, s, self.chunks[cid].payload)
        return sorted(fused.values(), key=lambda h: -h.score)[:limit]

    def is_weak(self, hits: list[ChunkHit]) -> bool:
        """True when nothing relevant was found: no keyword match and low vector similarity."""
        if not hits:
            return True
        return not any(h.keyword_score > 0 or h.vector_score >= self.min_vector_score for h in hits)

    def info(self) -> dict:
        return {"state": self.state, "error": self.error, "chunks": len(self.chunks), "model_id": self.embedder.model_id,
                "content_hash": self.content_hash}


def validate_dir(kb_dir: str):
    return validate_kb(load_kb(kb_dir))
