"""Native Ollama chat adapter (/api/chat). Used for local tiers so num_ctx and think can be set."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import LLMProvider, ProviderError, ProviderSpec, StreamChunk, ThinkStripper, ToolCall


class OllamaProvider(LLMProvider):
    def __init__(self, spec: ProviderSpec, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(spec)
        self._transport = transport

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            m = dict(m)
            if m.get("tool_calls"):   # OpenAI format -> Ollama format (arguments as object)
                calls = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args or "{}")
                        except json.JSONDecodeError:
                            args = {}
                    calls.append({"function": {"name": fn.get("name"), "arguments": args}})
                m["tool_calls"] = calls
            m.pop("tool_call_id", None)
            out.append(m)
        return out

    async def stream(self, messages, tools, max_tokens, temperature) -> AsyncIterator[StreamChunk]:
        url = self.spec.base_url.rstrip("/") + "/api/chat"
        options = {"num_ctx": self.spec.extra.get("num_ctx", 4096), "num_predict": max_tokens,
                   "temperature": temperature}
        body = {"model": self.spec.model, "messages": self._convert(messages), "stream": True, "options": options}
        if "think" in self.spec.extra:
            body["think"] = self.spec.extra["think"]
        if tools:
            body["tools"] = tools
        stripper = ThinkStripper() if self.spec.strip_thinking else None
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        calls: list[ToolCall] = []
        timeout = httpx.Timeout(120.0, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                async with client.stream("POST", url, json=body) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", "replace")
                        kind = "server" if resp.status_code >= 500 else "bad_request"
                        raise ProviderError(kind, f"ollama {resp.status_code}: {text[:200]}", status=resp.status_code)
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        obj = json.loads(line)
                        if obj.get("error"):
                            raise ProviderError("server", str(obj["error"])[:200])
                        msg = obj.get("message") or {}
                        if msg.get("thinking"):
                            yield StreamChunk("thinking")
                        piece = msg.get("content") or ""
                        if piece:
                            piece = stripper.feed(piece) if stripper else piece
                            if piece:
                                yield StreamChunk("text", text=piece)
                        for i, tc in enumerate(msg.get("tool_calls") or []):
                            fn = tc.get("function", {})
                            args = fn.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError as e:
                                    raise ProviderError("invalid_tool_call", "bad tool arguments") from e
                            calls.append(ToolCall(f"call_{len(calls)}", fn.get("name", ""), args))
                        if obj.get("done"):
                            usage = {"prompt_tokens": obj.get("prompt_eval_count", 0),
                                     "completion_tokens": obj.get("eval_count", 0)}
        except httpx.TimeoutException as e:
            raise ProviderError("timeout", "ollama timed out") from e
        except httpx.TransportError as e:
            raise ProviderError("server", f"ollama not reachable: {type(e).__name__}") from e
        if stripper:
            tail = stripper.flush()
            if tail:
                yield StreamChunk("text", text=tail)
        for tc in calls:
            yield StreamChunk("tool_call", tool_call=tc)
        yield StreamChunk("usage", usage=usage)
