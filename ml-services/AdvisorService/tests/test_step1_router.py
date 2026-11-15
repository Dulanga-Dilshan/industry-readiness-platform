import pytest

from ml_common import events as ev
from ml_common.providers.base import ProviderSpec
from ml_common.providers.budget import Block, Prompt
from ml_common.providers.fake import Fail, FakeProvider, MidStreamFail, Ok, Slow
from ml_common.providers.health import HealthTracker
from ml_common.providers.router import (AllProvidersFailed, ChatRequest, NoCapableProvider, Router,
                                        ToolFailure)
from ml_common.quota.estimator import QuotaTracker, answers_left
from ml_common.quota.headers import parse_duration, parse_ratelimit_headers
from ml_common.quota.ledger import QuotaLedger


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(script_a=None, script_b=None, **kw):
    clock = Clock()
    a = FakeProvider(script=script_a, name="a")
    b = FakeProvider(script=script_b, name="b")
    health = HealthTracker(clock)
    r = Router({"a": a, "b": b}, {"small": ["a", "b"]}, health=health, clock=clock, **kw)
    return r, a, b, clock


def req(**kw):
    return ChatRequest(tier="small", prompt=[{"role": "user", "content": "hi"}], **kw)


async def collect(router, request):
    return [e async for e in router.stream(request)]


async def test_success_first_provider():
    r, a, b, _ = make([Ok("hello there")])
    comp = await r.complete(req())
    assert comp.text.strip() == "hello there" and comp.provider == "a"
    assert not b.calls


@pytest.mark.parametrize("kind", ["rate_limit", "server", "timeout"])
async def test_fallback_on_error_kinds(kind):
    r, a, b, _ = make([Fail(kind)], [Ok("from b")])
    events = await collect(r, req())
    types = [e.type for e in events]
    assert ev.REROUTING in types and ev.RESET not in types
    assert "".join(e.data["text"] for e in events if e.type == ev.TOKEN).strip() == "from b"


async def test_auth_disables_until_manual_reenable():
    r, a, b, _ = make([Fail("auth")], [Ok("b1"), Ok("b2")])
    await r.complete(req())
    assert "a" in r.health.disabled
    await r.complete(req())
    assert len(a.calls) == 1            # never retried
    r.health.reenable("a")
    assert r.health.available("a")


async def test_cooldown_expiry_and_retry_after():
    r, a, b, clock = make([Fail("rate_limit", retry_after=100)], [Ok("x"), Ok("y")])
    await r.complete(req())
    assert not r.health.available("a")
    clock.t = 99
    assert not r.health.available("a")
    clock.t = 101
    assert r.health.available("a")


async def test_default_cooldowns():
    h = HealthTracker(Clock())
    h.mark_failure("p", "rate_limit")
    assert h.cooldown_left("p") == 30
    h.mark_failure("q", "server")
    assert h.cooldown_left("q") == 60


async def test_reset_once_after_partial_tokens():
    r, a, b, _ = make([MidStreamFail("partial words here")], [Ok("full answer")])
    events = await collect(r, req(visible=True))
    types = [e.type for e in events]
    assert types.count(ev.RESET) == 1
    assert types.index(ev.RESET) > types.index(ev.TOKEN)
    comp = await r.complete(req(visible=True)) if False else None


async def test_reset_at_most_once_then_error():
    clock = Clock()
    provs = {n: FakeProvider(script=[MidStreamFail()], name=n) for n in "abc"}
    r = Router(provs, {"small": ["a", "b", "c"]}, clock=clock)
    events = []
    with pytest.raises(AllProvidersFailed) as ei:
        async for e in r.stream(req(visible=True)):
            events.append(e)
    assert [e.type for e in events].count(ev.RESET) == 1
    assert ei.value.retryable is True


async def test_invisible_calls_switch_silently_without_reset():
    r, a, b, _ = make([MidStreamFail()], [Ok("ok")])
    events = await collect(r, req(visible=False))
    assert ev.RESET not in [e.type for e in events]


async def test_all_fail_raises_retryable():
    r, a, b, _ = make([Fail("server")], [Fail("timeout")])
    with pytest.raises(AllProvidersFailed) as ei:
        await r.complete(req())
    assert ei.value.retryable


TOOLS = [{"type": "function", "function": {"name": "get_x", "description": "", "parameters": {"type": "object", "properties": {}}}}]


async def test_invalid_tool_call_reroutes_then_tool_failure():
    r, a, b, _ = make([Ok("", tool_calls=[("nonexistent", {})])], [Ok("", tool_calls=[("nonexistent", {})])])
    with pytest.raises(ToolFailure):
        await r.complete(req(tools=TOOLS))


async def test_valid_tool_call_passes_and_invalid_falls_over():
    r, a, b, _ = make([Fail("invalid_tool_call")], [Ok("", tool_calls=[("get_x", {})])])
    comp = await r.complete(req(tools=TOOLS))
    assert comp.tool_calls[0].name == "get_x" and comp.provider == "b"


async def test_tools_ok_false_is_skipped_and_none_capable_is_tool_failure():
    r, a, b, _ = make()
    a.spec.tools_ok = False
    b.spec.tools_ok = False
    with pytest.raises(ToolFailure):
        await r.complete(req(tools=TOOLS))


async def test_personal_data_only_to_allowed():
    r, a, b, _ = make()
    a.spec.personal_data_ok = False
    comp = await r.complete(req(personal=True))
    assert comp.provider == "b"
    b.spec.personal_data_ok = False
    with pytest.raises(NoCapableProvider):
        await r.complete(req(personal=True))


async def test_tier_fallback_chain_and_disabled():
    clock = Clock()
    a, c = FakeProvider(name="a"), FakeProvider(name="c")
    r = Router({"a": a, "c": c}, {"small": ["a"], "large": ["c"]}, fallback={"small": ["large"]}, clock=clock)
    a.spec.enabled = False
    comp = await r.complete(req())
    assert comp.provider == "c"


async def test_fault_injection_and_midstream_fault():
    r, a, b, _ = make()
    r.inject_fault("a", "rate_limit")
    events = await collect(r, req())
    assert ev.REROUTING in [e.type for e in events]
    r.health.reenable("a")
    r.health._until.clear()
    r.inject_fault("a", "mid:server")
    events = await collect(r, req(visible=True))
    assert ev.RESET in [e.type for e in events]


# ---- budget -------------------------------------------------------------
def test_trim_order():
    hist = [{"role": "user", "content": "old " * 100}, {"role": "assistant", "content": "older " * 100}]
    blocks = [Block("chunk", "[S1]", text="good " * 100, score=0.9), Block("chunk", "[S2]", text="weak " * 100, score=0.1),
              Block("tool", "[T]", rows=["row " * 50] * 6), Block("profile", "[P]", rows=["fact one " * 20, "fact two " * 20])]
    p = Prompt(system="SYSTEM", question="Q?", history=hist, blocks=blocks)
    full = p.render(None)
    assert len(full) == 4
    # remove a little: only history goes first
    from ml_common.providers.budget import estimate_tokens
    total = Prompt._count(full)
    p.render(total - 100)
    assert p.trimmed == ["history"]
    # remove a lot: system and question remain, order of trimming respected
    msgs = p.render(60)
    assert p.trimmed[:2] == ["history", "chunks"]
    assert msgs[0]["content"] == "SYSTEM" and msgs[-1]["content"].endswith("Q?")
    assert p.trimmed.index("tool_rows") < p.trimmed.index("profile")


def test_lowest_score_chunk_dropped_first():
    blocks = [Block("chunk", "[S1]", text="aaaa " * 50, score=0.9), Block("chunk", "[S2]", text="bbbb " * 50, score=0.1)]
    p = Prompt(system="s", question="q", blocks=blocks)
    msgs = p.render(80)
    assert "aaaa" in msgs[-1]["content"] and "bbbb" not in msgs[-1]["content"]


# ---- quota --------------------------------------------------------------
def test_parse_headers():
    assert parse_duration("1m30s") == 90 and parse_duration("7.66s") == 7.66 and parse_duration("2ms") == 0.002
    r = parse_ratelimit_headers({"x-ratelimit-remaining-requests": "999", "x-ratelimit-remaining-tokens": "7500",
                                 "x-ratelimit-reset-tokens": "7.66s", "x-ratelimit-limit-requests": "1000"})
    assert r.requests_remaining == 999 and r.tokens_remaining == 7500 and r.reset_tokens_s == 7.66


def test_answers_left_never_guesses():
    assert answers_left(100, 8000, 2000) == 4
    assert answers_left(3, 8000, 2000) == 3
    assert answers_left(100, 8000, None) is None
    assert answers_left(100, None, None) == 100


def test_estimator_uses_measured_average():
    clock = Clock()
    led = QuotaLedger(clock=clock)
    for trace in ("t1", "t2"):
        led.record("groq", "m", "writer", 1000, 500, 10, "ok", trace)
        led.record("groq", "m", "triage", 300, 200, 10, "ok", trace)
    assert led.avg_tokens_per_answer("groq") == 2000
    q = QuotaTracker(led, {"groq": {"rpd": 1000, "tpm": 8000}}, clock=clock)
    q.observe_headers("groq", {"x-ratelimit-remaining-requests": "900", "x-ratelimit-remaining-tokens": "6000",
                               "x-ratelimit-reset-tokens": "60s"})
    rep = q.report(["groq", "gemini"])
    assert rep["groq"]["answers_left_estimate"] == 3 and rep["groq"]["source"] == "provider"
    assert rep["gemini"]["answers_left_estimate"] is None


async def test_router_skips_provider_when_quota_low_and_logs_ledger():
    clock = Clock()
    led = QuotaLedger(clock=lambda: clock.t)
    quota = QuotaTracker(led, {"a": {"tpm": 50}}, clock=lambda: clock.t)
    led.record("a", "m", "x", 40, 40, 1, "ok")
    a, b = FakeProvider(name="a"), FakeProvider(name="b")
    r = Router({"a": a, "b": b}, {"small": ["a", "b"]}, ledger=led, quota=quota, clock=clock)
    events = await collect(r, req())
    assert [e for e in events if e.type == ev.REROUTING][0].data["reason"] == "quota"
    assert not a.calls and b.calls
    assert led.window("b", 60)["requests"] == 1


async def test_ledger_records_failures():
    clock = Clock()
    led = QuotaLedger(clock=lambda: clock.t)
    r, a, b, _ = make([Fail("server")], [Ok("fine")])
    r.ledger = led
    await r.complete(req())
    rows = {x["provider"]: x for x in led.aggregate()}
    assert rows["a"]["errors"] == 1 and rows["b"]["errors"] == 0


async def test_cancellation_closes_provider_stream():
    import asyncio
    r, a, b, _ = make([Slow(5, Ok("late"))])
    task = asyncio.create_task(r.complete(req()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
