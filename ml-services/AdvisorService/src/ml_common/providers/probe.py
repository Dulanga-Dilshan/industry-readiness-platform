"""Capability probe: does the provider stream, and does it make valid tool calls?"""
from __future__ import annotations

from .base import LLMProvider, ProviderError

PROBE_TOOL = [{"type": "function", "function": {
    "name": "get_number", "description": "Return a number for a given label.",
    "parameters": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}}}]
PROBE_PROMPT = [{"role": "system", "content": "Use the provided tool when asked."},
                {"role": "user", "content": "Call the tool get_number with label 'alpha'."}]


async def probe_provider(provider: LLMProvider) -> dict:
    """Run two tiny calls. Spends a little quota, so it is manual (admin endpoint)."""
    result = {"name": provider.name, "streaming_ok": False, "tools_ok": False, "error": ""}
    try:
        text = ""
        async for c in provider.stream([{"role": "user", "content": "Reply with the single word: ok"}], None, 20, 0.0):
            if c.kind == "text":
                text += c.text
        result["streaming_ok"] = bool(text.strip())
    except ProviderError as e:
        result["error"] = f"stream: {e.kind}"
        return result
    try:
        calls = []
        async for c in provider.stream(PROBE_PROMPT, PROBE_TOOL, 80, 0.0):
            if c.kind == "tool_call" and c.tool_call:
                calls.append(c.tool_call)
        result["tools_ok"] = any(tc.name == "get_number" and isinstance(tc.arguments, dict)
                                 and "label" in tc.arguments for tc in calls)
    except ProviderError as e:
        result["error"] = f"tools: {e.kind}"
    return result
