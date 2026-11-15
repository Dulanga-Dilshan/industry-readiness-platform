"""One thin adapter over OpenAI-compatible endpoints (Groq, OpenRouter, Gemini compat, ...)."""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from .base import (LLMProvider, ProviderError, ProviderSpec, StreamChunk, ThinkStripper, ToolCall,
                   map_http_error)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 60.0


class OpenAICompatProvider(LLMProvider):
    def __init__(self, spec: ProviderSpec, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(spec)
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = os.environ.get(self.spec.api_key_env, "") if self.spec.api_key_env else ""
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _body(self, messages, tools, max_tokens, temperature) -> dict:
        body = {"model": self.spec.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "stream": True}
        if self.spec.extra.get("include_usage", True):
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
        body.update(self.spec.extra.get("extra_body", {}))
        return body

    async def stream(self, messages, tools, max_tokens, temperature) -> AsyncIterator[StreamChunk]:
        url = self.spec.base_url.rstrip("/") + "/chat/completions"
        stripper = ThinkStripper() if self.spec.strip_thinking else None
        partial: dict[int, dict] = {}
        usage = None
        resp_headers: dict[str, str] = {}
        timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                async with client.stream("POST", url, headers=self._headers(),
                                         json=self._body(messages, tools, max_tokens, temperature)) as resp:
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise map_http_error(resp.status_code, resp_headers, body)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("error"):
                            raise ProviderError("server", str(obj["error"])[:200], headers=resp_headers)
                        if obj.get("usage"):
                            usage = {"prompt_tokens": obj["usage"].get("prompt_tokens", 0),
                                     "completion_tokens": obj["usage"].get("completion_tokens", 0)}
                        for choice in obj.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("reasoning") or delta.get("reasoning_content"):
                                yield StreamChunk("thinking")
                            piece = delta.get("content")
                            if piece:
                                if stripper:
                                    piece = stripper.feed(piece)
                                if piece:
                                    yield StreamChunk("text", text=piece)
                            for tc in delta.get("tool_calls") or []:
                                slot = partial.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                                slot["id"] = tc.get("id") or slot["id"]
                                fn = tc.get("function") or {}
                                slot["name"] = fn.get("name") or slot["name"]
                                slot["args"] += fn.get("arguments") or ""
        except httpx.TimeoutException as e:
            raise ProviderError("timeout", "request timed out", headers=resp_headers) from e
        except httpx.TransportError as e:
            raise ProviderError("server", f"connection problem: {type(e).__name__}") from e
        if stripper:
            tail = stripper.flush()
            if tail:
                yield StreamChunk("text", text=tail)
        for i in sorted(partial):
            slot = partial[i]
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError as e:
                raise ProviderError("invalid_tool_call", "tool arguments were not valid JSON") from e
            yield StreamChunk("tool_call", tool_call=ToolCall(slot["id"] or f"call_{i}", slot["name"], args))
        yield StreamChunk("usage", usage=usage or {"prompt_tokens": 0, "completion_tokens": 0}, headers=resp_headers)
