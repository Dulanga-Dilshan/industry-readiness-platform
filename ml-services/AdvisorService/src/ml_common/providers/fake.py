"""Scripted fake provider used by every automated test (no network, no quota)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from .base import LLMProvider, ProviderError, ProviderSpec, StreamChunk, ToolCall


@dataclass
class Ok:
    text: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    prompt_tokens: int = 10
    completion_tokens: int = 10
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Fail:
    kind: str = "server"           # rate_limit | server | timeout | auth | credit | invalid_tool_call
    retry_after: float | None = None


@dataclass
class MidStreamFail:
    text_before: str = "partial answer "
    kind: str = "server"


@dataclass
class Slow:
    seconds: float = 0.2
    then: Any = None


class FakeProvider(LLMProvider):
    """Pops one scripted step per call. `responder(messages, tools)` can replace the script."""

    def __init__(self, spec: ProviderSpec | None = None, script: list | None = None,
                 default: Any = None, responder: Callable[[list[dict], list[dict] | None], Any] | None = None,
                 name: str = "fake"):
        super().__init__(spec or ProviderSpec(name=name, kind="fake", model=f"{name}-model", personal_data_ok=True))
        self.script = list(script or [])
        self.default = default if default is not None else Ok("fake answer")
        self.responder = responder
        self.calls: list[dict] = []

    def _next(self, messages, tools):
        if self.responder:
            return self.responder(messages, tools)
        return self.script.pop(0) if self.script else self.default

    async def stream(self, messages, tools, max_tokens, temperature) -> AsyncIterator[StreamChunk]:
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        step = self._next(messages, tools)
        if isinstance(step, Slow):
            await asyncio.sleep(step.seconds)
            step = step.then or Ok("slow answer")
        if isinstance(step, str):
            step = Ok(step)
        if isinstance(step, Fail):
            if step.kind == "invalid_tool_call":
                raise ProviderError("invalid_tool_call", "scripted invalid tool call")
            raise ProviderError(step.kind, "scripted failure", retry_after=step.retry_after)
        if isinstance(step, MidStreamFail):
            for word in step.text_before.split(" "):
                if word:
                    yield StreamChunk("text", text=word + " ")
            raise ProviderError(step.kind, "scripted mid-stream failure")
        assert isinstance(step, Ok), step
        for i, (name, args) in enumerate(step.tool_calls):
            yield StreamChunk("tool_call", tool_call=ToolCall(f"call_{i}", name, args))
        words = step.text.split(" ")
        for i, w in enumerate(words):
            if w == "" and len(words) == 1:
                continue
            yield StreamChunk("text", text=w + (" " if i < len(words) - 1 else ""))
        yield StreamChunk("usage", usage={"prompt_tokens": step.prompt_tokens,
                                          "completion_tokens": step.completion_tokens},
                          headers=step.headers or None)
