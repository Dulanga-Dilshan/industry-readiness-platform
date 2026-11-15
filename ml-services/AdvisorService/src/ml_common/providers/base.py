"""Provider contracts: spec, errors, stream chunks, abstract provider."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

ERROR_KINDS = {"rate_limit", "server", "timeout", "auth", "credit", "invalid_tool_call", "bad_request"}


class ProviderError(Exception):
    """Normalised provider failure. `kind` drives cooldowns and rerouting."""

    def __init__(self, kind: str, message: str = "", *, status: int | None = None,
                 retry_after: float | None = None, headers: dict[str, str] | None = None):
        super().__init__(message or kind)
        assert kind in ERROR_KINDS, kind
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.headers = headers or {}


@dataclass
class ProviderSpec:
    name: str
    kind: str                      # openai_compat | gemini | ollama | fake
    model: str
    base_url: str = ""
    api_key_env: str = ""          # NAME of the env var, never the key
    supports_tools: bool = True
    supports_streaming: bool = True
    context_window: int = 8000
    request_token_cap: int = 8000  # max tokens we send in one request
    rate_limits: dict[str, int] = field(default_factory=dict)  # rpm, rpd, tpm, tpd
    personal_data_ok: bool = False
    reasoning: bool = False
    strip_thinking: bool = False
    tools_ok: bool | None = None   # from the probe; None = not probed yet
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)  # num_ctx, think, extra_body ...

    def public_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("extra", None)
        d.pop("api_key_env", None)
        return d


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class StreamChunk:
    kind: str                       # text | thinking | tool_call | usage
    text: str = ""
    tool_call: ToolCall | None = None
    usage: dict[str, int] | None = None   # prompt_tokens, completion_tokens
    headers: dict[str, str] | None = None


class LLMProvider(ABC):
    def __init__(self, spec: ProviderSpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    @abstractmethod
    def stream(self, messages: list[dict], tools: list[dict] | None, max_tokens: int,
               temperature: float) -> AsyncIterator[StreamChunk]:
        """Async generator of StreamChunk. Raises ProviderError."""


class ThinkStripper:
    """Removes <think>...</think> text from a token stream (works across chunk borders)."""

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while self.buf:
            if self.inside:
                end = self.buf.find("</think>")
                if end == -1:
                    self.buf = self.buf[-8:]
                    return out
                self.buf = self.buf[end + 8:]
                self.inside = False
            else:
                start = self.buf.find("<think>")
                if start == -1:
                    keep = 0
                    for i in range(1, min(7, len(self.buf)) + 1):
                        if "<think>".startswith(self.buf[-i:]):
                            keep = i
                    out += self.buf[:len(self.buf) - keep]
                    self.buf = self.buf[len(self.buf) - keep:]
                    return out
                out += self.buf[:start]
                self.buf = self.buf[start + 7:]
                self.inside = True
        return out

    def flush(self) -> str:
        out = "" if self.inside else self.buf
        self.buf = ""
        return out


def map_http_error(status: int, headers: dict[str, str], body: str = "") -> ProviderError:
    ra = headers.get("retry-after")
    retry_after = None
    if ra:
        try:
            retry_after = float(ra)
        except ValueError:
            retry_after = None
    low = body.lower()
    if status == 429:
        return ProviderError("rate_limit", "rate limited", status=status, retry_after=retry_after, headers=headers)
    if status == 402 or "insufficient" in low and "credit" in low:
        return ProviderError("credit", "credit exhausted", status=status, headers=headers)
    if status in (401, 403):
        return ProviderError("auth", "authentication failed", status=status, headers=headers)
    if status >= 500:
        return ProviderError("server", f"server error {status}", status=status, retry_after=retry_after, headers=headers)
    return ProviderError("bad_request", f"bad request {status}: {body[:200]}", status=status, headers=headers)
