import httpx
import pytest

from advisor.tools.backend_client import BackendClient, ResultCache
from advisor.tools.backend_tools import BackendTools, register_backend_tools
from advisor.tools.base import ToolContext, ToolRegistry
from mock_backend.app import create_app

CFG = {"connect_timeout": 0.5, "read_timeout": 0.3, "retries": 2, "total_budget": 2.0, "stale_after_hours": 48}


class Env:
    def __init__(self, secret="test-secret"):
        self.app = create_app("test-secret", slow_seconds=1.0, timeout_seconds=5.0)
        self.transport = httpx.ASGITransport(app=self.app)
        self.sleeps = []

        async def fake_sleep(s):
            self.sleeps.append(s)
        self.client = BackendClient("http://mock", secret, lambda: CFG, self.transport, sleep=fake_sleep)
        self.tools = BackendTools(self.client)

    async def mode(self, m):
        async with httpx.AsyncClient(transport=self.transport, base_url="http://mock") as c:
            await c.post("/mock/mode", json={"mode": m})


@pytest.fixture
def env():
    return Env()


async def test_ok(env):
    r = await env.tools.get_cluster_demand("6m", "u1")
    assert r.status == "ok" and r.as_of and len(r.data["items"]) == 5
    assert "mock" not in r.data and "as_of" not in r.data


async def test_empty(env):
    await env.mode("empty")
    r = await env.tools.get_top_skills("Data & AI")
    assert r.status == "empty"


async def test_stale_shows_date(env):
    await env.mode("stale")
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "stale" and "last updated" in r.reason and r.data["items"]


async def test_malformed_is_error_and_discarded(env):
    await env.mode("malformed")
    r = await env.tools.get_top_skills("Data & AI")
    assert r.status == "error" and r.data is None
    r = await env.tools.get_job("x")
    assert r.status == "error"


@pytest.mark.parametrize("mode", ["404"])
async def test_not_built_yet(env, mode):
    await env.mode(mode)
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "unavailable" and "not available yet" in r.reason
    assert env.sleeps == []                      # no retries on 404/501


async def test_500_retries_then_unavailable(env):
    await env.mode("500")
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "unavailable" and len(env.sleeps) == 2     # two retries with backoff


async def test_429_respects_retry_after(env):
    await env.mode("429")
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "unavailable"
    assert env.sleeps and max(env.sleeps) >= 1.0


async def test_timeout_then_unavailable(env):
    await env.mode("timeout")
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "unavailable" and "in time" in r.reason or "too long" in r.reason


async def test_auth_error_no_retry_and_alert():
    env = Env()
    env.client.secret = "wrong"
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "error" and "credentials" in r.reason
    assert env.sleeps == [] and env.client.alerts


async def test_connection_refused():
    def boom(request):
        raise httpx.ConnectError("refused")
    sleeps = []

    async def sl(s):
        sleeps.append(s)
    c = BackendClient("http://x", "s", lambda: CFG, httpx.MockTransport(boom), sleep=sl)
    r = await BackendTools(c).get_cluster_demand("6m")
    assert r.status == "unavailable" and "reached" in r.reason and len(sleeps) == 2


async def test_last_good_served_as_stale_when_backend_fails(env):
    ok = await env.tools.get_cluster_demand("6m")
    assert ok.status == "ok"
    await env.mode("500")
    r = await env.tools.get_cluster_demand("6m")
    assert r.status == "stale" and "last saved result" in r.reason and r.data == ok.data
    r2 = await env.tools.get_cluster_demand("1y")            # different arguments: nothing cached
    assert r2.status == "unavailable"


async def test_breaker_opens_half_opens_and_closes(env):
    t = {"now": 0.0}
    env.client.clock = lambda: t["now"]
    env.client.breakers.clear()
    await env.mode("500")
    for _ in range(5):
        await env.tools.get_cluster_demand("6m")
    br = env.client.breaker("get_cluster_demand")
    assert br.state == "open"
    before = len(env.sleeps)
    r = await env.tools.get_cluster_demand("6m")             # open: answered immediately, no new HTTP attempts
    assert r.status == "unavailable" and len(env.sleeps) == before
    t["now"] = 31.0
    assert br.state == "half-open"
    await env.mode("ok")
    r = await env.tools.get_cluster_demand("6m")             # one probe succeeds
    assert r.status == "ok" and br.state == "closed"


async def test_half_open_probe_failure_reopens(env):
    t = {"now": 0.0}
    env.client.clock = lambda: t["now"]
    env.client.breakers.clear()
    await env.mode("500")
    for _ in range(5):
        await env.tools.get_cluster_demand("6m")
    t["now"] = 31.0
    await env.tools.get_cluster_demand("6m")                 # probe fails
    assert env.client.breaker("get_cluster_demand").state == "open"


async def test_trend_needs_exactly_one_of_cluster_or_skill(env):
    assert (await env.tools.get_demand_trend("1y")).status == "error"
    assert (await env.tools.get_demand_trend("1y", cluster="Data & AI", skill="Python")).status == "error"
    assert (await env.tools.get_demand_trend("1y", cluster="Data & AI")).data["series"]


async def test_jobs_and_job(env):
    r = await env.tools.search_jobs(query="python", limit=3)
    assert r.status == "ok" and len(r.data["items"]) == 3
    j = await env.tools.get_job("mock-1")
    assert j.data["job"]["description"]


async def test_registry_rejects_user_id_and_bad_args_and_loops(env):
    reg = ToolRegistry()
    register_backend_tools(reg, env.tools)
    ctx = ToolContext(user_id="u1", max_tool_calls=6)
    r = await reg.execute("get_top_skills", {"cluster": "Data & AI", "user_id": "someone-else"}, ctx)
    assert r.status == "error" and "unknown argument" in r.reason
    assert (await reg.execute("get_top_skills", {"cluster": "Nope"}, ctx)).status == "error"
    ok = await reg.execute("get_top_skills", {"cluster": "Data & AI"}, ctx)
    assert ok.status == "ok"
    loop = await reg.execute("get_top_skills", {"cluster": "Data & AI"}, ctx)
    assert "loop" in loop.reason
    small = ToolContext(max_tool_calls=1)
    await reg.execute("get_cluster_demand", {}, small)
    assert "limit" in (await reg.execute("get_cluster_demand", {"window": "1y"}, small)).reason
    assert (await reg.execute("nonexistent", {}, ctx)).status == "error"


def test_result_cache_expiry():
    t = {"n": 0.0}
    c = ResultCache(clock=lambda: t["n"])
    c.put("k", {"a": 1})
    assert c.get("k", 7) == {"a": 1}
    t["n"] = 8 * 86400
    assert c.get("k", 7) is None
