import asyncio
import json

import grpc
import httpx
import pytest

from advisor.server.grpc_service import ADMIN_METHODS, AdvisorServicer
from advisor.server.proto import advisor_pb2 as pb
from advisor.server.proto import advisor_pb2_grpc as pbg
from advisor.server.rest.api import create_rest_app
from advisor.eval.runner import EvalRunner, check
from ml_common import events as ev
from ml_common.auth import SecretInterceptor
from tests.helpers import make_app

MD = (("x-service-secret", "test-secret"),)
ADMIN = MD + (("x-admin-key", "admin"),)


@pytest.fixture
async def stack():
    app, brain = await make_app()
    srv = grpc.aio.server(interceptors=[SecretInterceptor("test-secret", "admin", ADMIN_METHODS)])
    pbg.add_AdvisorServiceServicer_to_server(AdvisorServicer(app), srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    await srv.start()
    chan = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    yield app, brain, pbg.AdvisorServiceStub(chan)
    await chan.close()
    await srv.stop(None)


async def test_grpc_ask_streams_public_events_only(stack):
    app, brain, stub = stack
    events = [e async for e in stub.Ask(pb.AskRequest(question="What is Docker?", user_id="u1"), metadata=MD)]
    types = [e.type for e in events]
    assert types[-1] == "done" and "token" in types and "source" in types
    assert not {"usage", "model", "tool_calls"} & set(types)
    assert json.loads(events[-1].data_json)["llm_calls"] == 1


async def test_grpc_profile_status_delete_and_extract(stack):
    app, brain, stub = stack
    p = pb.UserProfile(user_id="u1", current_semester="3.1", target_cluster="DevOps/Cloud", goals=["be a cloud engineer"],
                       consent={"store_memory": True},
                       facts=[pb.Fact(id="f1", type="goal", topic="career", text="wants cloud work", confidence=0.9)])
    assert (await stub.UpsertUserProfile(p, metadata=MD)).ok
    assert app.profiles.get("u1")["current_semester"] == "3.1"
    st = await stub.GetStatus(pb.Empty(), metadata=MD)
    assert st.index_state == "ready" and st.curriculum_status == "ok" and st.kb_chunks == 4
    brain.extract = '{"facts":[{"type":"goal","topic":"career","text":"wants cloud work soon","confidence":0.9}],"summary":"Talked about cloud."}'
    res = await stub.ExtractMemories(pb.ExtractRequest(user_id="u1", session_id="s", turns=[
        pb.Turn(role="user", content="I really want cloud work soon after graduating")]), metadata=MD)
    assert not res.skipped and res.facts[0].text == "wants cloud work soon" and res.summary.text == "Talked about cloud."
    assert (await stub.DeleteUser(pb.DeleteUserRequest(user_id="u1"), metadata=MD)).message == "deleted"
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await stub.UpsertUserProfile(pb.UserProfile(), metadata=MD)
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_grpc_admin_needs_admin_key_and_changes_models(stack):
    app, brain, stub = stack
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await stub.GetUsage(pb.UsageRequest(), metadata=MD)
    assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
    cfg = await stub.SetModel(pb.SetModelRequest(tier="small", order=["gemini-flash-lite", "groq-gpt-oss-20b"]), metadata=ADMIN)
    assert list(cfg.tiers[[t.tier for t in cfg.tiers].index("small")].providers) == ["gemini-flash-lite", "groq-gpt-oss-20b"]
    await stub.SetModel(pb.SetModelRequest(provider="groq-gpt-oss-20b", enabled=False), metadata=ADMIN)
    provs = {p.name: p for p in (await stub.ListProviders(pb.Empty(), metadata=ADMIN)).providers}
    assert provs["groq-gpt-oss-20b"].enabled is False
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await stub.SetModel(pb.SetModelRequest(tier="small", order=["nonexistent"]), metadata=ADMIN)
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    events = [x async for x in stub.Ask(pb.AskRequest(question="What is Docker?"), metadata=MD)]
    assert json.loads(events[-1].data_json)["providers"] == ["gemini-flash-lite"]      # new order is live, no restart
    usage = await stub.GetUsage(pb.UsageRequest(), metadata=ADMIN)
    assert usage.rows


async def test_grpc_client_cancel_stops_work(stack):
    app, brain, stub = stack
    from ml_common.providers.fake import Slow, Ok
    for p in app.providers.values():
        p.responder = None
        p.script = [Slow(5, Ok("late answer"))] * 3
    call = stub.Ask(pb.AskRequest(question="What is Docker?"), metadata=MD)
    first = await call.read()
    call.cancel()
    await asyncio.sleep(0.2)
    assert first.type == "status"


@pytest.fixture
async def rest():
    app, brain = await make_app()
    api = create_rest_app(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
        yield app, brain, c


H = {"X-Service-Secret": "test-secret"}
HA = {**H, "X-Admin-Key": "admin"}


async def test_rest_auth(rest):
    app, brain, c = rest
    assert (await c.get("/advisor/health")).status_code == 200
    assert (await c.get("/advisor/status")).status_code == 401
    assert (await c.get("/advisor/status", headers={"X-Service-Secret": "bad"})).status_code == 401
    assert (await c.get("/advisor/status", headers=H)).status_code == 200
    assert (await c.get("/advisor/admin/usage", headers=H)).status_code == 401
    assert (await c.get("/advisor/admin/usage", headers=HA)).status_code == 200


async def test_rest_sse_ask(rest):
    app, brain, c = rest
    async with c.stream("POST", "/advisor/ask", json={"question": "What is Docker?"}, headers=H) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [json.loads(l[6:]) async for l in r.aiter_lines() if l.startswith("data: ")]
    assert lines[-1]["type"] == "done" and any(l["type"] == "token" for l in lines)


async def test_rest_admin_config_keys_never_returned_and_secrets_refused(rest):
    app, brain, c = rest
    r = await c.get("/advisor/admin/config", headers=HA)
    body = json.dumps(r.json())
    assert "API_KEY" not in body.replace("api_key_env", "") and "test-secret" not in body
    bad = await c.put("/advisor/admin/config", json={"api_key": "x"}, headers=HA)
    assert bad.status_code == 400
    ok = await c.put("/advisor/admin/config", json={"cache": {"enabled": True}}, headers=HA)
    assert ok.json()["cache"]["enabled"] is True


async def test_rest_quota_metrics_faults_validate(rest):
    app, brain, c = rest
    await c.post("/advisor/ask", json={"question": "What is Docker?"}, headers=H)
    q = (await c.get("/advisor/admin/quota", headers=HA)).json()
    assert "groq-gpt-oss-20b" in q
    m = (await c.get("/advisor/admin/metrics", headers=HA)).text
    assert "advisor_requests_total" in m
    f = await c.post("/advisor/admin/faults", json={"provider": "groq-gpt-oss-20b", "kind": "rate_limit"}, headers=HA)
    assert f.json()["pending"]["groq-gpt-oss-20b"] == 1
    r = await c.post("/advisor/ask", json={"question": "What is Docker?"}, headers=H)
    assert "rerouting" in r.text
    v = (await c.post("/advisor/admin/validate", headers=HA)).json()
    assert v["curriculum_status"] == "ok"


async def test_rest_profile_and_delete(rest):
    app, brain, c = rest
    assert (await c.put("/advisor/profile", json={"user_id": "u9", "current_semester": "3.1"}, headers=H)).json()["ok"]
    assert app.profiles.get("u9")
    assert (await c.delete("/advisor/user/u9", headers=H)).json()["deleted"] is True


async def test_eval_runner_passes_on_fakes():
    app, brain = await make_app()
    brain.writer = lambda m: "Software Engineering has 400 postings, 40% of the total [S1]."
    r = EvalRunner(app)
    run_id = r.start({"ids": ["k01", "x01", "x03", "x05", "c02", "d06"]})["run_id"]
    for _ in range(100):
        if r.runs[run_id]["state"] == "done":
            break
        await asyncio.sleep(0.05)
    run = r.runs[run_id]
    assert run["state"] == "done"
    failed = [x for x in run["results"] if not x["passed"]]
    assert failed == [], failed
    assert len(r.load_questions()) >= 30


async def test_capacity_test():
    app, brain = await make_app()
    res = await EvalRunner(app).capacity_test({"concurrent": 6})
    assert res["errors"] == 0 and res["concurrent"] == 6


def test_check_flags_problems():
    item = {"expect": {"intent": "data", "tools_include": ["get_top_skills"], "sources_min": 1}}
    events = [ev.Event(ev.DONE, {"intent": "knowledge", "tools": [], "llm_calls": 1})]
    fails = check(item, events)
    assert len(fails) == 3
