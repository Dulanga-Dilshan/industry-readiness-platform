"""REST + SSE facade (same events as gRPC) and admin endpoints. Auth: X-Service-Secret, plus X-Admin-Key for admin."""
from __future__ import annotations

import asyncio 
import json
import logging
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from advisor.config.runtime_config import ConfigError
from advisor.curriculum.validator import load_curriculum
from advisor.knowledge.bootstrap import validate_dir
from advisor.orchestrator import AskInput
from ml_common.auth import secrets_match
from ml_common.quota.metrics import render_metrics

log = logging.getLogger("advisor.rest")
audit = logging.getLogger("ml_common.audit")


class AskBody(BaseModel):
    question: str
    user_id: str = ""
    session_id: str = ""
    turns: list[dict] = Field(default_factory=list)
    tier_override: str = ""
    remember: bool = True


class ProfileBody(BaseModel):
    user_id: str
    program_id: str = ""
    current_semester: str = ""
    target_cluster: str = ""
    self_assessed_skills: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    facts: list[dict] = Field(default_factory=list)
    summaries: list[dict] = Field(default_factory=list)
    consent: dict = Field(default_factory=dict)


def create_rest_app(app) -> FastAPI:
    #api = FastAPI(title="Advisor", docs_url=None, redoc_url=None, openapi_url=None)
    api = FastAPI(title="Advisor")
    s = app.settings

    def need_secret(x_service_secret: str | None = Header(None)):
        if not secrets_match(x_service_secret, s.service_secret):
            raise HTTPException(401, "unauthorized")

    def need_admin(x_service_secret: str | None = Header(None), x_admin_key: str | None = Header(None)):
        need_secret(x_service_secret)
        if not secrets_match(x_admin_key, s.admin_key):
            audit.warning("admin REST call rejected")
            raise HTTPException(401, "unauthorized")

    P = "/advisor"

    @api.get(f"{P}/health")
    async def health():
        return {"ok": True, "index": app.kb.state}

    @api.post(f"{P}/ask", dependencies=[Depends(need_secret)])
    async def ask(body: AskBody):
        inp = AskInput(body.question, body.user_id, body.session_id, body.turns, body.tier_override, body.remember)

        async def gen():
            try:
                async for e in app.ask_events(inp):
                    if e.public:
                        yield f"data: {json.dumps(e.to_dict(), ensure_ascii=False, default=str)}\n\n"
            except asyncio.CancelledError:
                raise                        # client went away: stop all work
            except Exception:
                log.exception("ask failed")
                yield 'data: {"type":"error","data":{"code":"internal","message":"Something went wrong.","retryable":true}}\n\n'
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.put(f"{P}/profile", dependencies=[Depends(need_secret)])
    async def put_profile(body: ProfileBody):
        await app.upsert_profile(body.model_dump())
        return {"ok": True}

    @api.delete(f"{P}/user/{{user_id}}", dependencies=[Depends(need_secret)])
    async def delete_user(user_id: str):
        return {"ok": True, "deleted": await app.delete_user(user_id)}

    @api.post(f"{P}/memories/extract", dependencies=[Depends(need_secret)])
    async def extract(body: dict):
        res = await app.extract_memories(body.get("user_id", ""), body.get("session_id", ""), body.get("turns", []),
                                         body.get("remember", True))
        return res

    @api.get(f"{P}/status", dependencies=[Depends(need_secret)])
    async def status():
        return app.status()

    # ---- admin ----------------------------------------------------------------------
    @api.get(f"{P}/admin/config", dependencies=[Depends(need_admin)])
    async def get_config():
        return {"config": app.rc.snapshot(), "providers": app.provider_infos()}

    @api.put(f"{P}/admin/config", dependencies=[Depends(need_admin)])
    async def put_config(patch: dict):
        try:
            snap = app.rc.update(patch)
        except ConfigError as e:
            raise HTTPException(400, str(e))
        app.apply_runtime()
        audit.info("config updated: keys=%s", list(patch))
        return snap

    @api.put(f"{P}/admin/model", dependencies=[Depends(need_admin)])
    async def set_model(body: dict):
        try:
            snap = app.set_model(body.get("tier"), body.get("order"), body.get("provider"), body.get("enabled"),
                                 body.get("personal_data_ok"))
        except ConfigError as e:
            raise HTTPException(400, str(e))
        audit.info("model changed: %s", {k: v for k, v in body.items()})
        return snap

    @api.get(f"{P}/admin/providers", dependencies=[Depends(need_admin)])
    async def providers():
        return app.provider_infos()

    @api.post(f"{P}/admin/probe", dependencies=[Depends(need_admin)])
    async def probe(body: dict | None = None):
        return await app.probe((body or {}).get("providers"))

    @api.post(f"{P}/admin/reindex", dependencies=[Depends(need_admin)])
    async def reindex():
        return await app.reindex(force=True)

    @api.get(f"{P}/admin/usage", dependencies=[Depends(need_admin)])
    async def usage(since_seconds: int = 0):
        return app.usage(since_seconds)

    @api.get(f"{P}/admin/quota", dependencies=[Depends(need_admin)])
    async def quota():
        return app.quota_report()

    @api.get(f"{P}/admin/metrics", dependencies=[Depends(need_admin)], response_class=PlainTextResponse)
    async def metrics():
        return render_metrics(app.ledger, app.health, app.quota, list(app.providers), app.router.reroutes)

    @api.post(f"{P}/admin/faults", dependencies=[Depends(need_admin)])
    async def faults(body: dict):
        """Test only: make a provider fail. kinds: rate_limit, server, timeout, auth, mid:<kind>. {"clear": true} removes all."""
        if body.get("clear"):
            app.router.clear_faults()
        else:
            app.router.inject_fault(body["provider"], body.get("kind", "rate_limit"), int(body.get("count", 1)))
        return {"ok": True, "pending": {k: len(v) for k, v in app.router.faults.items()}}

    @api.post(f"{P}/admin/validate", dependencies=[Depends(need_admin)])
    async def validate():
        kb = [str(i) for i in validate_dir(s.knowledge_dir)]
        cur = load_curriculum(s.curriculum_path, s.glossary_path)
        return {"knowledge_issues": kb, "curriculum_status": cur.status, "curriculum_issues": [str(i) for i in cur.issues]}

    @api.post(f"{P}/admin/cache/clear", dependencies=[Depends(need_admin)])
    async def clear_cache():
        app.cache.clear()
        return {"ok": True}

    if s.enable_embedding_admin:
        @api.post(f"{P}/admin/embedding/test", dependencies=[Depends(need_admin)])
        async def embed_test(body: dict):
            vecs = await app.embedder.embed(body.get("texts", ["hello"]))
            return {"model": app.embedder.model_id, "dim": len(vecs[0]), "count": len(vecs)}

        @api.post(f"{P}/admin/embedding/switch", dependencies=[Depends(need_admin)])
        async def embed_switch(body: dict):
            """Change the embedding model, then rebuild the index. Queries are refused until the rebuild finishes."""
            from ml_common.embeddings.registry import build_embedder
            app.rc.update({"embedding": {"provider": body["provider"], "model": body["model"]}})
            app.embedder = build_embedder(body["provider"], body["model"], s.ollama_base_url)
            app.kb.embedder = app.embedder
            app.user_memory.embedder = app.embedder
            app.cache.embedder = app.embedder
            return await app.reindex(force=True)

    # ---- evaluation ---------------------------------------------------------------------------
    from advisor.eval.runner import EvalRunner
    runner = EvalRunner(app)

    @api.get(f"{P}/eval/questions", dependencies=[Depends(need_admin)])
    async def eval_questions():
        return runner.load_questions()

    @api.post(f"{P}/eval/run", dependencies=[Depends(need_admin)])
    async def eval_run(body: dict | None = None):
        return runner.start(body or {})

    @api.get(f"{P}/eval/runs/{{run_id}}", dependencies=[Depends(need_admin)])
    async def eval_status(run_id: str):
        r = runner.runs.get(run_id)
        if not r:
            raise HTTPException(404, "unknown run")
        return r

    @api.post(f"{P}/admin/capacity", dependencies=[Depends(need_admin)])
    async def capacity(body: dict | None = None):
        return await runner.capacity_test(body or {})

    return api
