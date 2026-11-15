"""gRPC servicer: thin translation layer between protobuf and AdvisorApp."""
from __future__ import annotations

import json
import logging

import grpc

from advisor.config.runtime_config import ConfigError
from advisor.orchestrator import AskInput
from advisor.server.proto import advisor_pb2 as pb
from advisor.server.proto import advisor_pb2_grpc as pbg
from ml_common import events as ev

log = logging.getLogger("advisor.grpc")
ADMIN_METHODS = {"ReindexKnowledge", "SetModel", "GetModelConfig", "ListProviders", "ProbeProviders", "GetUsage"}


def fact_to_dict(f) -> dict:
    return {"id": f.id, "type": f.type, "topic": f.topic, "text": f.text, "confidence": f.confidence,
            "source_session": f.source_session, "timestamp": f.timestamp}


def dict_to_fact(d: dict) -> pb.Fact:
    return pb.Fact(id=d.get("id", ""), type=d.get("type", ""), topic=d.get("topic", ""), text=d.get("text", ""),
                   confidence=float(d.get("confidence", 0)), source_session=d.get("source_session", ""),
                   timestamp=d.get("timestamp", ""))


def profile_from_pb(p: pb.UserProfile) -> dict:
    return {"user_id": p.user_id, "program_id": p.program_id, "current_semester": p.current_semester,
            "target_cluster": p.target_cluster, "self_assessed_skills": list(p.self_assessed_skills),
            "goals": list(p.goals), "constraints": list(p.constraints), "facts": [fact_to_dict(f) for f in p.facts],
            "summaries": [{"id": s.id, "session_id": s.session_id, "text": s.text, "timestamp": s.timestamp}
                          for s in p.summaries],
            "consent": dict(p.consent)}


def provider_info(d: dict) -> pb.ProviderInfo:
    info = pb.ProviderInfo(name=d["name"], kind=d["kind"], model=d["model"], enabled=d["enabled"],
                           supports_tools=d["supports_tools"], supports_streaming=d["supports_streaming"],
                           personal_data_ok=d["personal_data_ok"], cooling_down=d["cooling_down"],
                           cooldown_seconds=d["cooldown_seconds"], disabled_reason=d["disabled_reason"],
                           context_window=d["context_window"])
    if d.get("tools_ok") is not None:
        info.tools_ok = d["tools_ok"]
    return info


def model_config(app) -> pb.ModelConfig:
    snap = app.rc.snapshot()
    return pb.ModelConfig(tiers=[pb.TierOrder(tier=t, providers=p) for t, p in snap["tiers"].items()],
                          providers=[provider_info(d) for d in app.provider_infos()],
                          tool_failure_message=snap["tool_failure_message"])


class AdvisorServicer(pbg.AdvisorServiceServicer):
    def __init__(self, app):
        self.app = app

    async def Ask(self, request, context):
        inp = AskInput(question=request.question, user_id=request.user_id, session_id=request.session_id,
                       turns=[{"role": t.role, "content": t.content} for t in request.turns],
                       tier_override=request.tier_override,
                       remember=request.remember if request.HasField("remember") else True)
        try:
            async for e in self.app.ask_events(inp):
                if e.public:
                    yield pb.AskEvent(type=e.type, data_json=json.dumps(e.data, ensure_ascii=False, default=str))
        except Exception:
            log.exception("ask failed")
            yield pb.AskEvent(type=ev.ERROR, data_json=json.dumps(
                {"code": "internal", "message": "Something went wrong. Please try again.", "retryable": True}))

    async def UpsertUserProfile(self, request, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
        await self.app.upsert_profile(profile_from_pb(request))
        return pb.Ack(ok=True)

    async def DeleteUser(self, request, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
        existed = await self.app.delete_user(request.user_id)
        return pb.Ack(ok=True, message="deleted" if existed else "nothing stored")

    async def ExtractMemories(self, request, context):
        remember = request.remember if request.HasField("remember") else True
        res = await self.app.extract_memories(request.user_id, request.session_id,
                                              [{"role": t.role, "content": t.content} for t in request.turns], remember)
        out = pb.ExtractResult(skipped=res["skipped"], reason=res["reason"],
                               facts=[dict_to_fact(f) for f in res["facts"]],
                               merged_facts=[dict_to_fact(f) for f in res["merged"]])
        if res["summary"]:
            s = res["summary"]
            out.summary.CopyFrom(pb.Summary(id=s["id"], session_id=s["session_id"], text=s["text"], timestamp=s["timestamp"]))
        return out

    async def ReindexKnowledge(self, request, context):
        res = await self.app.reindex(force=True)
        return pb.Ack(ok=res.get("state") == "ready", message=json.dumps(res))

    async def GetStatus(self, request, context):
        st = self.app.status()
        return pb.Status(version="0.1.0", index_state=st["index"]["state"], kb_chunks=st["index"]["chunks"],
                         embedding_model=st["index"]["model_id"], curriculum_status=st["curriculum"]["status"],
                         curriculum_reason=st["curriculum"]["reason"], enabled_providers=st["enabled_providers"])

    async def SetModel(self, request, context):
        try:
            self.app.set_model(request.tier or None, list(request.order) if request.tier else None,
                               request.provider or None, request.enabled if request.HasField("enabled") else None,
                               request.personal_data_ok if request.HasField("personal_data_ok") else None)
        except ConfigError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        return model_config(self.app)

    async def GetModelConfig(self, request, context):
        return model_config(self.app)

    async def ListProviders(self, request, context):
        return pb.ProviderList(providers=[provider_info(d) for d in self.app.provider_infos()])

    async def ProbeProviders(self, request, context):
        res = await self.app.probe(list(request.providers) or None)
        return pb.ProbeResult(entries=[pb.ProbeEntry(name=r["name"], streaming_ok=r["streaming_ok"],
                                                     tools_ok=r["tools_ok"], error=r["error"]) for r in res])

    async def GetUsage(self, request, context):
        u = self.app.usage(request.since_seconds)
        return pb.UsageReport(reroutes=u["reroutes"], rows=[pb.UsageRow(
            provider=r["provider"], agent=r["agent"], requests=r["requests"], tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"], errors=r["errors"], avg_latency_ms=r["avg_latency_ms"]) for r in u["rows"]])
