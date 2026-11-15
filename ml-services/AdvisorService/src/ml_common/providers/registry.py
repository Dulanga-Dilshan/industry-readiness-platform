"""Build provider objects from specs."""
from __future__ import annotations

import httpx

from .base import LLMProvider, ProviderSpec
from .fake import FakeProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider


def build_provider(spec: ProviderSpec, transport: httpx.AsyncBaseTransport | None = None) -> LLMProvider:
    if spec.kind == "openai_compat":
        return OpenAICompatProvider(spec, transport)
    if spec.kind == "gemini":
        return GeminiProvider(spec, transport)
    if spec.kind == "ollama":
        return OllamaProvider(spec, transport)
    if spec.kind == "fake":
        return FakeProvider(spec)
    raise ValueError(f"unknown provider kind: {spec.kind}")


def build_registry(specs: list[ProviderSpec], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, LLMProvider]:
    return {s.name: build_provider(s, transport) for s in specs}
