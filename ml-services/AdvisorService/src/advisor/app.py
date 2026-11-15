"""AdvisorApp: builds and holds every component. Tests inject fakes; production reads env."""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
from dataclasses import replace

import httpx

from ml_common.embeddings.base import Embedder
from ml_common.embeddings.fake import FakeEmbedder
from ml_common.embeddings.registry import build_embedder
from ml_common.providers.base import LLMProvider, ProviderSpec
from ml_common.providers.budget import Prompt
from ml_common.providers.fake import FakeProvider, Ok
from ml_common.providers.health import HealthTracker
from ml_common.providers.probe import probe_provider
from ml_common.providers.registry import build_registry
from ml_common.providers.router import ChatRequest, Router
from ml_common.quota.estimator import QuotaTracker
from ml_common.quota.ledger import QuotaLedger
from ml_common.vectorstore.base import VectorStore
from ml_common.vectorstore.memory_store import MemoryVectorStore
from ml_common.vectorstore.qdrant_store import QdrantVectorStore

from advisor.cache.semantic_cache import SemanticCache
from advisor.config.runtime_config import RuntimeConfig
from advisor.config.settings import Settings, default_providers
from advisor.curriculum.tools import CurriculumTools
from advisor.curriculum.validator import load_curriculum, load_glossary
from advisor.knowledge.bootstrap import KnowledgeIndex
from advisor.knowledge.loader import load_kb
from advisor.knowledge.tools import search_knowledge, search_user_memory
from advisor.memory.extract import (EXTRACT_SYSTEM, clean_summary, merge_facts, parse_json, validate_facts)
from advisor.memory.profile_store import ProfileStore
from advisor.memory.user_memory import UserMemory
from advisor.orchestrator import AskInput, Orchestrator
from advisor.safety.guardrails import RateLimiter
from advisor.text.spellcheck import SpellCorrector
from advisor.tools.backend_client import BackendClient, ResultCache
from advisor.tools.backend_tools import BackendTools, register_backend_tools
from advisor.tools.base import Tool, ToolContext, ToolRegistry
from advisor.tools.tavily import TavilyProvider
from advisor.tools.web_search import WebSearchService
from advisor.tools.wikipedia import WikipediaProvider
from advisor.config.settings import CLUSTERS, WINDOWS

log = logging.getLogger("advisor.app")


class AdvisorApp:
    def __init__(self, settings: Settings, *, providers: dict[str, LLMProvider] | None = None,
                 embedder: Embedder | None = None, store: VectorStore | None = None,
                 backend_transport: httpx.AsyncBaseTransport | None = None, web_providers: dict | None = None,
                 provider_transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        data = settings.data_dir
        os.makedirs(data, exist_ok=True) if data and data != ":memory:" else None
        db = lambda n: ":memory:" if data == ":memory:" else os.path.join(data, n)
        self.specs = default_providers()
        self.rc = RuntimeConfig(None if data == ":memory:" else os.path.join(data, "runtime_config.json"),
                                {s.name for s in self.specs} | set((providers or {}).keys()))
        if providers is None:
            if settings.use_fakes:
                providers = {s.name: FakeProvider(replace(s, kind="fake", enabled=True, personal_data_ok=True),
                                                  default=Ok("fake answer")) for s in self.specs}
            else:
                for s in self.specs:
                    if s.kind == "ollama":
                        s.base_url = settings.ollama_base_url
                providers = build_registry(self.specs, provider_transport)
        self.providers = providers
        self.ledger = QuotaLedger(db("ledger.sqlite"))
        limits = {n: p.spec.rate_limits for n, p in providers.items() if p.spec.rate_limits}
        self.quota = QuotaTracker(self.ledger, limits)
        self.health = HealthTracker()
        self.router = Router(providers, {}, health=self.health, ledger=self.ledger, quota=self.quota)
        self.apply_runtime()

        emb_cfg = dict(self.rc.get("embedding", default={}))
        if settings.embedding_provider:
            emb_cfg["provider"] = settings.embedding_provider
        if settings.embedding_model:
            emb_cfg["model"] = settings.embedding_model
        self.embedder = embedder or (FakeEmbedder() if settings.use_fakes else
                                     build_embedder(emb_cfg.get("provider", "ollama"), emb_cfg.get("model", "nomic-embed-text"),
                                                    settings.ollama_base_url))
        self.store = store or (QdrantVectorStore(url=settings.qdrant_url) if settings.qdrant_url else
                               QdrantVectorStore(location=":memory:") if settings.use_fakes else MemoryVectorStore())
        self.kb = KnowledgeIndex(self.store, self.embedder, settings.knowledge_dir)
        self.curriculum_state = load_curriculum(settings.curriculum_path, settings.glossary_path)
        self.curriculum = CurriculumTools(self.curriculum_state)
        self.backend_client = BackendClient(settings.backend_base_url, settings.service_secret,
                                            lambda: self.rc.get("backend", default={}), backend_transport,
                                            cache=ResultCache(db("tool_cache.sqlite")))
        self.backend = BackendTools(self.backend_client)
        wp = web_providers if web_providers is not None else {"wikipedia": WikipediaProvider(),
                                                              "tavily": TavilyProvider(settings.tavily_api_key_env)}
        self.web = WebSearchService(wp, order=lambda: self.rc.get("web", "order", default=["wikipedia", "tavily"]),
                                    cache_hours=lambda: self.rc.get("web", "cache_hours", default=24))
        self.profiles = ProfileStore(db("profiles.sqlite"))
        self.user_memory = UserMemory(self.store, self.embedder)
        glossary = load_glossary(settings.glossary_path)
        self.known_terms = {g.lower() for g in glossary}
        self.spell = SpellCorrector(glossary)
        self._add_spell_terms()
        lim = self.rc.get("limits", default={})
        self.limiter = RateLimiter(lim.get("per_user_per_10min", 20), 600, lim.get("global_per_day", 2000))
        self.cache = SemanticCache(self.embedder, lambda: self.rc.get("cache", "threshold", default=0.95),
                                   lambda: self.rc.get("cache", "ttl_seconds", default=86400))
        self.registry = ToolRegistry()
        self._register_tools()
        self.orchestrator = Orchestrator(self)
        self._index_task: asyncio.Task | None = None
        self.started = time.time()

    # ---- runtime config -> router ------------------------------------------------
    def apply_runtime(self) -> None:
        self.router.tiers = {k: list(v) for k, v in self.rc.get("tiers").items()}
        self.router.fallback = {k: list(v) for k, v in self.rc.get("fallback").items()}
        for name, ov in self.rc.get("providers", default={}).items():
            p = self.providers.get(name)
            if p:
                for k, v in ov.items():
                    setattr(p.spec, k, v)

    def _add_spell_terms(self) -> None:
        terms = list(CLUSTERS) + [m.name for m in (self.curriculum_state.curriculum.semesters[i].modules[j]
                                                   for i in range(len(self.curriculum_state.curriculum.semesters))
                                                   for j in range(len(self.curriculum_state.curriculum.semesters[i].modules)))] \
            if self.curriculum_state.curriculum else list(CLUSTERS)
        terms += [str(f.meta.get("title", "")) for f in load_kb(self.settings.knowledge_dir)]
        self.spell.add_terms(terms)
        self.known_terms |= {t.lower() for t in terms if len(t) > 2}

    # ---- tools ------------------------------------------------------------------------
    def _register_tools(self) -> None:
        r = self.registry
        register_backend_tools(r, self.backend)

        async def sk(a, ctx):
            return await search_knowledge(self.kb, a)

        async def wsr(a, ctx):
            return await self.web.search(a["query"], a.get("kind", "general"), ctx.profile)

        async def wf(a, ctx):
            return await self.web.fetch(a["url"])

        async def cp(a, ctx):
            return self.curriculum.get_curriculum_progress(a.get("semester"))

        async def sg(a, ctx):
            async def top(cluster, window, limit):
                return await self.backend.get_top_skills(cluster, window, limit, ctx.user_id)
            return await self.curriculum.get_skill_gaps(a.get("semester"), a["cluster"], top, a.get("window", "6m"))

        async def um(a, ctx):
            return await search_user_memory(self.user_memory, a, ctx)

        s = lambda **kw: {"type": "string", **kw}
        r.register(Tool("search_knowledge", "Search the approved knowledge base.",
                        {"type": "object", "properties": {"query": s(maxLength=300), "cluster": s(enum=CLUSTERS),
                                                          "type": s(enum=["framework", "guide", "tool", "recruiter", "faq"])},
                         "required": ["query"]}, sk, "knowledge"))
        r.register(Tool("web_search", "Search the web or Wikipedia.",
                        {"type": "object", "properties": {"query": s(maxLength=200), "kind": s(enum=["definition", "general"])},
                         "required": ["query"]}, wsr, "web"))
        r.register(Tool("web_fetch", "Fetch a page returned by web_search.",
                        {"type": "object", "properties": {"url": s(maxLength=500)}, "required": ["url"]}, wf, "web"))
        r.register(Tool("get_curriculum_progress", "Completed, current and upcoming modules for a semester.",
                        {"type": "object", "properties": {"semester": s(maxLength=8)}, "required": ["semester"]}, cp, "curriculum"))
        r.register(Tool("get_skill_gaps", "Market-demanded skills not yet covered by the student's modules.",
                        {"type": "object", "properties": {"semester": s(maxLength=8), "cluster": s(enum=CLUSTERS),
                                                          "window": s(enum=WINDOWS)}, "required": ["semester", "cluster"]},
                        sg, "curriculum"))
        r.register(Tool("search_user_memory", "Search this student's remembered facts.",
                        {"type": "object", "properties": {"query": s(maxLength=300)}, "required": ["query"]}, um, "memory"))

    # ---- lifecycle -------------------------------------------------------------------------
    async def start(self) -> None:
        self._index_task = asyncio.create_task(self.kb.ensure())     # never blocks startup

    async def reindex(self, force: bool = True) -> dict:
        res = await self.kb.ensure(force=force)
        self.cache.clear()
        return res

    def status(self) -> dict:
        return {"index": self.kb.info(), "curriculum": {"status": self.curriculum_state.status,
                                                        "reason": self.curriculum_state.reason},
                "enabled_providers": [n for n, p in self.providers.items() if p.spec.enabled]}

    # ---- profile / memory ----------------------------------------------------------------------
    async def upsert_profile(self, profile: dict) -> None:
        self.profiles.put(profile)
        try:
            await self.user_memory.sync(profile, self.rc.get("memory", "retention_days", default=180))
        except Exception as e:     # an embedding outage must not block the profile update
            log.warning("memory sync failed: %s", type(e).__name__)

    async def delete_user(self, user_id: str) -> bool:
        existed = self.profiles.delete(user_id)
        await self.user_memory.delete_user(user_id)
        self.limiter.hits.pop(user_id, None)
        return existed

    async def extract_memories(self, user_id: str, session_id: str, turns: list[dict], remember: bool = True) -> dict:
        profile = self.profiles.get(user_id) or {"user_id": user_id, "facts": []}
        if not remember or profile.get("consent", {}).get("store_memory") is False:
            return {"skipped": True, "reason": "memory not allowed", "facts": [], "merged": profile.get("facts", []), "summary": None}
        user_text = " ".join(t["content"] for t in turns if t.get("role") == "user")
        if len(user_text.strip()) < 20:
            return {"skipped": True, "reason": "nothing to remember", "facts": [], "merged": profile.get("facts", []), "summary": None}
        personal = profile.get("consent", {}).get("store_memory") is not None
        convo = "\n".join(f"{t['role'].upper()}: {t['content'][:600]}" for t in turns[-12:])
        try:
            comp = await self.router.complete(ChatRequest(
                tier="small", prompt=Prompt(system=EXTRACT_SYSTEM, question=convo), max_tokens=400, agent="memory",
                personal=True))
        except Exception as e:
            return {"skipped": True, "reason": f"model unavailable ({type(e).__name__})", "facts": [],
                    "merged": profile.get("facts", []), "summary": None}
        obj = parse_json(comp.text) or {}
        new = validate_facts(obj.get("facts") or [], user_text, session_id)
        cap = self.rc.get("memory", "fact_cap", default=50)
        merged = merge_facts(profile.get("facts", []), new, cap)
        summary_text = clean_summary(obj.get("summary", ""), self.rc.get("memory", "summary_cap_chars", default=600))
        summary = None
        if summary_text:
            from datetime import datetime, timezone
            import uuid
            summary = {"id": uuid.uuid4().hex[:12], "session_id": session_id, "text": summary_text,
                       "timestamp": datetime.now(timezone.utc).isoformat()}
        return {"skipped": False, "reason": "", "facts": new, "merged": merged, "summary": summary}

    # ---- admin -------------------------------------------------------------------------------------
    def provider_infos(self) -> list[dict]:
        out = []
        for n, p in self.providers.items():
            d = p.spec.public_dict()
            d.update(self.health.state(n))
            out.append(d)
        return out

    async def probe(self, names: list[str] | None = None) -> list[dict]:
        names = names or [n for n, p in self.providers.items() if p.spec.enabled]
        out = []
        for n in names:
            if n in self.providers:
                res = await probe_provider(self.providers[n])
                self.providers[n].spec.tools_ok = res["tools_ok"] if res["streaming_ok"] else None
                out.append(res)
        return out

    def set_model(self, tier: str | None, order: list[str] | None, provider: str | None, enabled: bool | None,
                  personal_data_ok: bool | None) -> dict:
        if tier and order is not None:
            tiers = copy.deepcopy(self.rc.get("tiers"))
            tiers[tier] = order
            self.rc.update({"tiers": tiers})
        if provider:
            self.rc.set_provider(provider, enabled=enabled, personal_data_ok=personal_data_ok)
            if enabled:
                self.health.reenable(provider)
        self.apply_runtime()
        return self.rc.snapshot()

    def usage(self, since_seconds: int = 0) -> dict:
        return {"rows": self.ledger.aggregate(since_seconds or None), "reroutes": self.router.reroutes}

    def quota_report(self) -> dict:
        return self.quota.report(list(self.providers))

    async def ask_events(self, req: AskInput):
        async for e in self.orchestrator.ask(req):
            yield e


def build_app(settings: Settings | None = None, **kw) -> AdvisorApp:
    return AdvisorApp(settings or Settings.from_env(), **kw)
