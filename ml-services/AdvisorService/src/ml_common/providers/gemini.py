"""Native Gemini adapter (streamGenerateContent over SSE) with function calling."""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from .base import LLMProvider, ProviderError, ProviderSpec, StreamChunk, ToolCall, map_http_error

BASE = "https://generativelanguage.googleapis.com/v1beta"


def to_gemini(messages: list[dict]) -> tuple[dict | None, list[dict]]:
    """OpenAI-style messages -> (systemInstruction, contents)."""
    system_parts, contents = [], []
    id_to_name: dict[str, str] = {}
    for m in messages:
        role = m.get("role")
        if role == "system":
            system_parts.append({"text": m.get("content") or ""})
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
        elif role == "assistant":
            parts = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                id_to_name[tc.get("id", "")] = fn.get("name", "")
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif role == "tool":
            name = m.get("name") or id_to_name.get(m.get("tool_call_id", ""), "tool")
            try:
                payload = json.loads(m.get("content") or "{}")
                if not isinstance(payload, dict):
                    payload = {"result": payload}
            except json.JSONDecodeError:
                payload = {"result": m.get("content")}
            contents.append({"role": "user", "parts": [{"functionResponse": {"name": name, "response": payload}}]})
    system = {"parts": system_parts} if system_parts else None
    return system, contents


def tools_to_gemini(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    decls = []
    for t in tools:
        fn = t["function"]
        decls.append({"name": fn["name"], "description": fn.get("description", ""),
                      "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    return [{"functionDeclarations": decls}]


class GeminiProvider(LLMProvider):
    def __init__(self, spec: ProviderSpec, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(spec)
        self._transport = transport

    async def stream(self, messages, tools, max_tokens, temperature) -> AsyncIterator[StreamChunk]:
        base = self.spec.base_url or BASE
        url = f"{base.rstrip('/')}/models/{self.spec.model}:streamGenerateContent?alt=sse"
        key = os.environ.get(self.spec.api_key_env, "") if self.spec.api_key_env else ""
        system, contents = to_gemini(messages)
        body: dict = {"contents": contents,
                      "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
        if system:
            body["systemInstruction"] = system
        gt = tools_to_gemini(tools)
        if gt:
            body["tools"] = gt
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        calls: list[ToolCall] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0), transport=self._transport) as client:
                async with client.stream("POST", url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                         json=body) as resp:
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", "replace")
                        raise map_http_error(resp.status_code, hdrs, text)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            obj = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        um = obj.get("usageMetadata")
                        if um:
                            usage = {"prompt_tokens": um.get("promptTokenCount", 0),
                                     "completion_tokens": um.get("candidatesTokenCount", 0)}
                        for cand in obj.get("candidates", []):
                            for part in (cand.get("content") or {}).get("parts", []):
                                if part.get("thought"):
                                    yield StreamChunk("thinking")
                                elif "text" in part and part["text"]:
                                    yield StreamChunk("text", text=part["text"])
                                elif "functionCall" in part:
                                    fc = part["functionCall"]
                                    calls.append(ToolCall(f"call_{len(calls)}", fc.get("name", ""), fc.get("args", {}) or {}))
        except httpx.TimeoutException as e:
            raise ProviderError("timeout", "gemini timed out") from e
        except httpx.TransportError as e:
            raise ProviderError("server", f"gemini connection problem: {type(e).__name__}") from e
        for tc in calls:
            yield StreamChunk("tool_call", tool_call=tc)
        yield StreamChunk("usage", usage=usage)
