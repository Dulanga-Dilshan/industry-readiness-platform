"""Router: tiers, fallback chain, cooldowns, reset handling, token budgeting, quota ledger."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from .. import events as ev
from ..events import Event
from .base import LLMProvider, ProviderError, ToolCall
from .budget import Prompt, estimate_tokens
from .health import HealthTracker

log = logging.getLogger("ml_common.router")

REASON_TEXT = {"rate_limit": "busy", "server": "busy", "timeout": "timeout", "auth": "quota",
               "credit": "quota", "invalid_tool_call": "tool", "bad_request": "error"}


class AllProvidersFailed(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class ToolFailure(AllProvidersFailed):
    """No provider could use the tools correctly. Caller shows TOOL_FAILURE_MESSAGE."""


class NoCapableProvider(AllProvidersFailed):
    pass


@dataclass
class ChatRequest:
    tier: str
    prompt: Prompt | list[dict]
    tools: list[dict] | None = None
    max_tokens: int = 700
    temperature: float = 0.2
    agent: str = "agent"
    trace_id: str = ""
    personal: bool = False        # request carries student data
    visible: bool = False         # tokens go straight to the student (enables reset events)


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    tier: str = ""
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})
    events: list[Event] = field(default_factory=list)   # rerouting / reset notices to forward
    thinking: bool = False


class Router:
    def __init__(self, providers: dict[str, LLMProvider], tiers: dict[str, list[str]],
                 fallback: dict[str, list[str]] | None = None, health: HealthTracker | None = None,
                 ledger=None, quota=None, clock: Callable[[], float] = time.monotonic,
                 require_probe_for_tools: bool = False):
        self.providers = providers
        self.tiers = tiers
        self.fallback = fallback or {}
        self.health = health or HealthTracker(clock)
        self.ledger = ledger
        self.quota = quota
        self.require_probe = require_probe_for_tools
        self.faults: dict[str, list[str]] = {}
        self.reroutes = 0

    # ---- faults (testing) -------------------------------------------------
    def inject_fault(self, provider: str, kind: str = "rate_limit", count: int = 1) -> None:
        self.faults.setdefault(provider, []).extend([kind] * count)

    def clear_faults(self) -> None:
        self.faults.clear()

    # ---- candidate selection ---------------------------------------------
    def tier_chain(self, tier: str) -> list[str]:
        chain = [tier] + [t for t in self.fallback.get(tier, []) if t != tier]
        return chain

    def _eligible(self, name: str, req: ChatRequest) -> str | None:
        p = self.providers.get(name)
        if p is None:
            return "unknown"
        s = p.spec
        if not s.enabled:
            return "disabled"
        if not self.health.available(name):
            return "cooldown"
        if req.tools:
            if not s.supports_tools or s.tools_ok is False:
                return "no_tools"
            if self.require_probe and s.tools_ok is None:
                return "not_probed"
        if req.personal and not s.personal_data_ok:
            return "personal_data"
        return None

    def candidates(self, req: ChatRequest) -> tuple[list[str], dict[str, str]]:
        seen, out, skipped = set(), [], {}
        for tier in self.tier_chain(req.tier):
            for name in self.tiers.get(tier, []):
                if name in seen:
                    continue
                seen.add(name)
                why = self._eligible(name, req)
                if why:
                    skipped[name] = why
                    continue
                out.append(name)
        return out, skipped

    def can_serve(self, tier: str, *, tools: bool = False, personal: bool = False) -> bool:
        cands, _ = self.candidates(ChatRequest(tier=tier, prompt=[], tools=[{}] if tools else None, personal=personal))
        return bool(cands)

    def _render(self, req: ChatRequest, spec) -> list[dict]:
        if isinstance(req.prompt, Prompt):
            return req.prompt.render(spec.request_token_cap)
        return req.prompt

    # ---- streaming --------------------------------------------------------
    async def stream(self, req: ChatRequest) -> AsyncIterator[Event]:
        cands, skipped = self.candidates(req)
        if not cands:
            if req.tools:
                tool_skips = {"no_tools", "not_probed"}
                if any(v in tool_skips for v in skipped.values()):
                    raise ToolFailure("no provider can use tools")
            if req.personal and any(v == "personal_data" for v in skipped.values()):
                raise NoCapableProvider("no provider allowed for personal data", retryable=False)
            raise NoCapableProvider(f"no provider available ({skipped})")

        resets_used = 0
        invalid_calls = 0
        last_kind = ""
        tried = 0
        for idx, name in enumerate(cands):
            provider = self.providers[name]
            spec = provider.spec
            messages = self._render(req, spec)
            est = sum(estimate_tokens(str(m.get("content") or "")) for m in messages)
            if self.quota and self.quota.is_low(name, est) and idx < len(cands) - 1:
                yield Event(ev.REROUTING, {"reason": "quota", "from": name})
                self.reroutes += 1
                continue
            tried += 1
            streamed = False
            started = time.monotonic()
            text_out, usage = "", {"prompt_tokens": 0, "completion_tokens": 0}
            tool_calls: list[ToolCall] = []
            headers = None
            thinking_sent = False
            try:
                fault = self.faults.get(name)
                mid_fault = None
                if fault:
                    kind = fault.pop(0)
                    if kind.startswith("mid:"):
                        mid_fault = kind[4:]
                    else:
                        raise ProviderError(kind, "injected fault", retry_after=5 if kind == "rate_limit" else None)
                agen = provider.stream(messages, req.tools, req.max_tokens, req.temperature)
                try:
                    async for chunk in agen:
                        if chunk.kind == "text":
                            streamed = True
                            text_out += chunk.text
                            yield Event(ev.TOKEN, {"text": chunk.text})
                            if mid_fault:
                                raise ProviderError(mid_fault, "injected mid-stream fault")
                        elif chunk.kind == "thinking":
                            if not thinking_sent:
                                thinking_sent = True
                                yield Event(ev.STATUS, {"text": "thinking"})
                        elif chunk.kind == "tool_call" and chunk.tool_call:
                            tool_calls.append(chunk.tool_call)
                        elif chunk.kind == "usage":
                            usage = chunk.usage or usage
                            headers = chunk.headers
                finally:
                    await agen.aclose()
                # validate tool calls against the offered tools
                if tool_calls:
                    offered = {t["function"]["name"] for t in (req.tools or [])}
                    if any(tc.name not in offered or not isinstance(tc.arguments, dict) for tc in tool_calls):
                        raise ProviderError("invalid_tool_call", "model asked for a nonexistent tool")
            except ProviderError as e:
                last_kind = e.kind
                latency = (time.monotonic() - started) * 1000
                self._record(name, spec.model, req, usage, latency, e.kind, text_out, est)
                if e.headers and self.quota:
                    self.quota.observe_headers(name, e.headers)
                if e.kind == "invalid_tool_call":
                    invalid_calls += 1
                else:
                    self.health.mark_failure(name, e.kind, e.retry_after)
                self.reroutes += 1
                has_next = idx < len(cands) - 1
                if streamed and req.visible:
                    if resets_used >= 1 or not has_next:
                        raise AllProvidersFailed("failed after partial answer", retryable=True) from e
                    resets_used += 1
                    yield Event(ev.RESET, {})
                if has_next:
                    yield Event(ev.REROUTING, {"reason": REASON_TEXT.get(e.kind, "error"), "from": name})
                continue
            latency = (time.monotonic() - started) * 1000
            self.health.mark_success(name)
            if headers and self.quota:
                self.quota.observe_headers(name, headers)
            self._record(name, spec.model, req, usage, latency, "ok", text_out, est)
            yield Event(ev.USAGE, usage)
            yield Event(ev.MODEL, {"provider": name, "model": spec.model})
            if tool_calls:
                yield Event(ev.TOOL_CALLS, {"calls": tool_calls})
            return
        if req.tools and invalid_calls and last_kind == "invalid_tool_call":
            raise ToolFailure("tool calls were invalid on every provider")
        raise AllProvidersFailed(f"all providers failed (last: {last_kind or 'skipped'})", retryable=True)

    def _record(self, name, model, req, usage, latency, status, text_out, est_in=0) -> None:
        if not self.ledger:
            return
        tin = usage.get("prompt_tokens") or est_in
        tout = usage.get("completion_tokens") or (estimate_tokens(text_out) if text_out else 0)
        self.ledger.record(name, model, req.agent, tin, tout, latency, status, req.trace_id)

    # ---- non-streaming helper --------------------------------------------
    async def complete(self, req: ChatRequest) -> Completion:
        comp = Completion(tier=req.tier)
        async for e in self.stream(req):
            if e.type == ev.TOKEN:
                comp.text += e.data["text"]
            elif e.type == ev.RESET:
                comp.text = ""
                comp.events.append(e)
            elif e.type == ev.REROUTING:
                comp.events.append(e)
            elif e.type == ev.USAGE:
                comp.usage = e.data
            elif e.type == ev.MODEL:
                comp.provider, comp.model = e.data["provider"], e.data["model"]
            elif e.type == ev.TOOL_CALLS:
                comp.tool_calls = e.data["calls"]
            elif e.type == ev.STATUS:
                comp.thinking = True
        return comp
