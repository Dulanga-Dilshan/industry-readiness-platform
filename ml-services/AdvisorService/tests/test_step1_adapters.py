import json

import httpx
import pytest

from ml_common.providers.base import ProviderError, ProviderSpec, ThinkStripper
from ml_common.providers.gemini import GeminiProvider, to_gemini
from ml_common.providers.ollama import OllamaProvider
from ml_common.providers.openai_compat import OpenAICompatProvider


def sse(*objs):
    body = "".join(f"data: {json.dumps(o)}\n\n" for o in objs) + "data: [DONE]\n\n"
    return body


async def gather(p, tools=None):
    return [c async for c in p.stream([{"role": "user", "content": "hi"}], tools, 50, 0.1)]


def spec(kind="openai_compat", **kw):
    return ProviderSpec(name="t", kind=kind, model="m", base_url="http://x/v1", **kw)


async def test_openai_stream_text_usage_and_headers():
    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(200, text=sse(
            {"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
            headers={"x-ratelimit-remaining-requests": "99"})
    p = OpenAICompatProvider(spec(), httpx.MockTransport(handler))
    chunks = await gather(p)
    assert "".join(c.text for c in chunks if c.kind == "text") == "Hello"
    last = chunks[-1]
    assert last.usage == {"prompt_tokens": 5, "completion_tokens": 2}
    assert last.headers["x-ratelimit-remaining-requests"] == "99"


async def test_openai_tool_call_assembled_from_deltas():
    def handler(request):
        return httpx.Response(200, text=sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_x", "arguments": '{"a":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' 1}'}}]}}]}))
    chunks = await gather(OpenAICompatProvider(spec(), httpx.MockTransport(handler)), tools=[{}])
    tc = [c.tool_call for c in chunks if c.kind == "tool_call"][0]
    assert tc.name == "get_x" and tc.arguments == {"a": 1}


async def test_openai_bad_tool_json_is_invalid_tool_call():
    def handler(request):
        return httpx.Response(200, text=sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "x", "arguments": "{bad"}}]}}]}))
    with pytest.raises(ProviderError) as ei:
        await gather(OpenAICompatProvider(spec(), httpx.MockTransport(handler)))
    assert ei.value.kind == "invalid_tool_call"


@pytest.mark.parametrize("status,kind", [(429, "rate_limit"), (500, "server"), (401, "auth"), (402, "credit")])
async def test_openai_http_errors(status, kind):
    def handler(request):
        return httpx.Response(status, text="no", headers={"retry-after": "7"})
    with pytest.raises(ProviderError) as ei:
        await gather(OpenAICompatProvider(spec(), httpx.MockTransport(handler)))
    assert ei.value.kind == kind
    if status == 429:
        assert ei.value.retry_after == 7


async def test_openai_timeout_maps():
    def handler(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(ProviderError) as ei:
        await gather(OpenAICompatProvider(spec(), httpx.MockTransport(handler)))
    assert ei.value.kind == "timeout"


async def test_think_text_stripped():
    def handler(request):
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "<think>secret"}}]},
                                            {"choices": [{"delta": {"content": " plan</think>Answer"}}]}))
    chunks = await gather(OpenAICompatProvider(spec(strip_thinking=True), httpx.MockTransport(handler)))
    assert "".join(c.text for c in chunks if c.kind == "text") == "Answer"


def test_think_stripper_split_tag():
    s = ThinkStripper()
    out = s.feed("a<th") + s.feed("ink>hidden</thi") + s.feed("nk>b") + s.flush()
    assert out == "ab"


async def test_ollama_native_stream_sets_num_ctx_and_think():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        lines = [{"message": {"thinking": "hmm"}}, {"message": {"content": "Hi"}},
                 {"message": {"tool_calls": [{"function": {"name": "get_x", "arguments": {"q": 1}}}]}},
                 {"done": True, "prompt_eval_count": 7, "eval_count": 3, "message": {}}]
        return httpx.Response(200, text="\n".join(json.dumps(l) for l in lines))
    p = OllamaProvider(spec("ollama", extra={"num_ctx": 4096, "think": False}), httpx.MockTransport(handler))
    chunks = await gather(p, tools=[{}])
    assert seen["options"]["num_ctx"] == 4096 and seen["think"] is False
    kinds = [c.kind for c in chunks]
    assert "thinking" in kinds and "tool_call" in kinds
    assert chunks[-1].usage == {"prompt_tokens": 7, "completion_tokens": 3}


async def test_ollama_unreachable_is_server_error():
    def handler(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(ProviderError) as ei:
        await gather(OllamaProvider(spec("ollama"), httpx.MockTransport(handler)))
    assert ei.value.kind == "server"


def test_gemini_message_conversion():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{\"a\": 1}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "{\"ok\": true}"}]
    system, contents = to_gemini(msgs)
    assert system["parts"][0]["text"] == "S"
    assert contents[1]["parts"][0]["functionCall"] == {"name": "f", "args": {"a": 1}}
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "f"


async def test_gemini_stream_text_and_function_call():
    def handler(request):
        assert request.headers["x-goog-api-key"] is not None
        objs = [{"candidates": [{"content": {"parts": [{"text": "Hi"}]}}]},
                {"candidates": [{"content": {"parts": [{"functionCall": {"name": "get_x", "args": {"a": 2}}}]}}],
                 "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 1}}]
        return httpx.Response(200, text="".join(f"data: {json.dumps(o)}\n\n" for o in objs))
    p = GeminiProvider(ProviderSpec(name="g", kind="gemini", model="gemini-x"), httpx.MockTransport(handler))
    chunks = await gather(p, tools=[{"type": "function", "function": {"name": "get_x", "parameters": {}}}])
    assert any(c.kind == "text" and c.text == "Hi" for c in chunks)
    assert [c.tool_call for c in chunks if c.kind == "tool_call"][0].arguments == {"a": 2}
    assert chunks[-1].usage["prompt_tokens"] == 4
