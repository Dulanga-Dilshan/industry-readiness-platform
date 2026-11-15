import os

import pytest

from advisor.knowledge.bootstrap import IndexMismatchError, KnowledgeIndex
from advisor.knowledge.chunker import chunk_file
from advisor.knowledge.loader import load_kb, parse_front_matter
from advisor.knowledge.validator import validate_kb
from ml_common.embeddings.base import EmbeddingError
from ml_common.embeddings.fake import FakeEmbedder
from ml_common.vectorstore.memory_store import MemoryVectorStore
from ml_common.vectorstore.qdrant_store import QdrantVectorStore

KB = os.path.join(os.path.dirname(__file__), "fixtures", "kb_dev")


def make(store=None, emb=None, kb=KB):
    return KnowledgeIndex(store or MemoryVectorStore(), emb or FakeEmbedder(), kb)


async def test_only_approved_files_indexed():
    idx = make()
    res = await idx.ensure()
    assert res["state"] == "ready"
    assert not any("SECRETDRAFTCONTENT" in c.text for c in idx.chunks.values())
    assert idx.info()["chunks"] == 4
    assert await idx.store.count("kb") == 4


async def test_hybrid_exact_term_match_and_semantic():
    idx = make()
    await idx.ensure()
    hits = await idx.search("CCNA", limit=3)
    assert hits[0].payload["file_id"] == "dev-ccna" and hits[0].keyword_score > 0
    hits = await idx.search("how do I package an app into a container image")
    assert hits[0].payload["file_id"] == "dev-docker"


async def test_filters_by_cluster_and_type():
    idx = make()
    await idx.ensure()
    hits = await idx.search("certification", cluster="Networking & Infrastructure")
    assert all(h.payload["cluster"] in ("Networking & Infrastructure", "all", "core") for h in hits)
    hits = await idx.search("tests defects", type="faq")
    assert hits and all(h.payload["type"] == "faq" for h in hits)


async def test_weak_detection():
    idx = make()
    await idx.ensure()
    assert idx.is_weak(await idx.search("zzzqqq unrelated gibberish xylophone"))
    assert not idx.is_weak(await idx.search("docker container"))


async def test_model_mismatch_refused_and_rebuilt():
    store = MemoryVectorStore()
    idx = make(store, FakeEmbedder(dim=64, model_id="model-a"))
    await idx.ensure()
    idx.embedder = FakeEmbedder(dim=64, model_id="model-b")      # someone switched the model without re-index
    with pytest.raises(IndexMismatchError):
        await idx.search("docker")
    res = await idx.ensure()                                      # rebuild replaces everything
    assert res["embedded_now"] == 4
    assert (await store.get_meta("kb"))["model_id"] == "model-b"
    assert await idx.search("docker")


async def test_not_ready_while_building_and_failed_state():
    emb = FakeEmbedder()
    emb.fail_next = 10
    idx = make(emb=emb)
    with pytest.raises(RuntimeError):
        await idx.search("x")
    res = await idx.ensure()
    assert res["state"] == "failed" and idx.state == "failed"
    with pytest.raises(RuntimeError):
        await idx.search("x")


async def test_resume_after_interruption_only_embeds_missing(tmp_path):
    import shutil
    kb = tmp_path / "kb"
    shutil.copytree(KB, kb)
    store, emb = MemoryVectorStore(), FakeEmbedder()
    idx = KnowledgeIndex(store, emb, str(kb), batch_size=1)
    # simulate an interruption: one file embedded, then a failure
    files = [f for f in load_kb(str(kb)) if f.approved]
    from ml_common.vectorstore.base import Point
    await store.ensure_collection("kb", emb.dim)
    await store.set_meta("kb", {"model_id": emb.model_id, "dim": emb.dim, "status": "building"})
    first = files[0]
    chunks = chunk_file(first)
    vecs = await emb.embed([c.text for c in chunks])
    await store.upsert("kb", [Point(c.id, v, c.payload) for c, v in zip(chunks, vecs)])
    res = await idx.ensure()
    assert res["state"] == "ready"
    assert res["embedded_now"] == 4 - len(chunks)
    assert await store.count("kb") == 4


async def test_incremental_update_by_file_hash(tmp_path):
    import shutil
    kb = tmp_path / "kb"
    shutil.copytree(KB, kb)
    idx = KnowledgeIndex(MemoryVectorStore(), FakeEmbedder(), str(kb))
    await idx.ensure()
    again = await idx.ensure()
    assert again["embedded_now"] == 0
    p = kb / "guides" / "ccna.md"
    p.write_text(p.read_text().replace("routing, switching", "routing, wireless, switching"))
    changed = await idx.ensure()
    assert changed["embedded_now"] == 1
    os.remove(p)
    await idx.ensure()
    assert await idx.store.count("kb") == 3


async def test_works_with_qdrant_local_mode():
    idx = make(QdrantVectorStore(location=":memory:"))
    assert (await idx.ensure())["state"] == "ready"
    hits = await idx.search("CCNA subnetting")
    assert hits[0].payload["file_id"] == "dev-ccna"


def test_front_matter_errors_are_readable(tmp_path):
    (tmp_path / "a.md").write_text("no front matter here\n## H\nbody body body body body body\n")
    (tmp_path / "b.md").write_text("---\nid: x\ntitle: T\ntype: nonsense\ncluster: Nowhere\nstatus: maybe\n---\n## H\nbody\n")
    (tmp_path / "c.md").write_text("---\nid: x\ntitle: [unclosed\n---\nbody\n")
    issues = [str(i) for i in validate_kb(load_kb(str(tmp_path)))]
    text = "\n".join(issues)
    assert "a.md:1" in text and "front matter missing" in text
    assert "type 'nonsense'" in text and "status 'maybe'" in text and "cluster 'Nowhere'" in text
    assert "last_reviewed" in text and "not valid YAML" in text


def test_real_knowledge_base_has_no_errors():
    real = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
    errors = [i for i in validate_kb(load_kb(real)) if i.level == "error"]
    assert errors == [], [str(e) for e in errors]


def test_chunker_skips_todo_sections():
    real = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", "framework")
    for f in load_kb(real):
        assert chunk_file(f) == [], f.rel     # skeletons contain only TODO notes
