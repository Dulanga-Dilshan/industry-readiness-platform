from __future__ import annotations

from .base import Embedder
from .fake import FakeEmbedder
from .gemini import GeminiEmbedder
from .ollama import OllamaEmbedder


def build_embedder(provider: str, model: str, ollama_base_url: str = "http://ollama:11434",
                   api_key_env: str = "GEMINI_API_KEY") -> Embedder:
    if provider == "ollama":
        return OllamaEmbedder(ollama_base_url, model)
    if provider == "gemini":
        return GeminiEmbedder(model, api_key_env)
    if provider == "fake":
        return FakeEmbedder()
    raise ValueError(f"unknown embedding provider: {provider}")
