"""Environment settings and module-level constants. No argparse: everything has a default."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ml_common.providers.base import ProviderSpec

API_PREFIX = "/advisor"
VERSION = "0.1.0"

CLUSTERS = ["Software Engineering", "DevOps/Cloud", "Cybersecurity", "Networking & Infrastructure", "Data & AI"]
WINDOWS = ["24h", "30d", "6m", "1y"]

DEFAULT_TOOL_FAILURE_MESSAGE = ("I'm having trouble using my tools with the current model, so I can't answer this "
                                "reliably. Please try again or switch the model.")

# ---- backend tool constants (overridable in runtime config) -----------------
CONNECT_TIMEOUT_S = 2.0
READ_TIMEOUT_S = 5.0
RETRIES = 2
RETRY_BACKOFF_S = (0.3, 1.0)
TOTAL_BUDGET_S = 8.0
STALE_AFTER_HOURS = 48
CACHE_KEEP_DAYS = 7
BREAKER_FAILURES = 5
BREAKER_OPEN_S = 30.0

# ---- providers -----------------------------------------------------------------
def default_providers() -> list[ProviderSpec]:
    """Env var NAMES below are the conventional ones; change them if your .env uses other names."""
    return [
        ProviderSpec(name="groq-gpt-oss-20b", kind="openai_compat", model="openai/gpt-oss-20b",
                     base_url="https://api.groq.com/openai/v1", api_key_env="GROQ_API_KEY",
                     context_window=8000, request_token_cap=6000, rate_limits={"rpd": 1000, "tpm": 8000},
                     reasoning=True),
        ProviderSpec(name="gemini-flash-lite", kind="gemini", model="gemini-flash-lite-latest",
                     base_url="https://generativelanguage.googleapis.com/v1beta", api_key_env="GEMINI_API_KEY",
                     context_window=100000, request_token_cap=30000),
        ProviderSpec(name="openrouter-free", kind="openai_compat", model="cohere/north-mini-code:free",
                     base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY",
                     context_window=32000, request_token_cap=16000, rate_limits={"rpm": 20, "rpd": 50}),
        # local entries: defined, disabled until Ollama is set up
        ProviderSpec(name="ollama-mistral-7b", kind="ollama", model="mistral:7b", base_url="", enabled=False,
                     personal_data_ok=True, context_window=8192, request_token_cap=6000,
                     extra={"num_ctx": 8192, "think": False}),
        ProviderSpec(name="ollama-qwen3-8b", kind="ollama", model="qwen3:8b", base_url="", enabled=False,
                     personal_data_ok=True, context_window=8192, request_token_cap=6000,
                     strip_thinking=True, extra={"num_ctx": 8192, "think": False}),
        ProviderSpec(name="ollama-deepseek-r1-8b", kind="ollama", model="deepseek-r1:8b", base_url="",
                     enabled=False, personal_data_ok=True, reasoning=True, strip_thinking=True,
                     context_window=8192, request_token_cap=6000, extra={"num_ctx": 8192, "think": True}),
    ]


DEFAULT_TIERS = {
    "small": ["groq-gpt-oss-20b", "gemini-flash-lite", "openrouter-free"],
    # only small models were tested, so "large" reuses them in another order until a bake-off picks more
    "large": ["gemini-flash-lite", "groq-gpt-oss-20b", "openrouter-free"],
    "local-fast": ["ollama-mistral-7b", "ollama-qwen3-8b"],
    "local-reason": ["ollama-deepseek-r1-8b"],
}
DEFAULT_FALLBACK = {"small": ["large"], "large": ["small"], "local-fast": [], "local-reason": ["local-fast"]}

DEFAULT_RUNTIME = {
    "tiers": DEFAULT_TIERS,
    "fallback": DEFAULT_FALLBACK,
    "providers": {},   # name -> overrides: enabled, personal_data_ok, tools_ok
    "difficulty": {"long_question_words": 25, "hard_words": ["compare", "plan", "explain why", "what should i learn",
                                                             "roadmap", "step by step", "which is better"],
                   "tier_by_difficulty": {"easy": "small", "medium": "small", "hard": "large"},
                   "advice_tier": "large"},
    "budgets": {"llm_calls": {"easy": 2, "medium": 4, "hard": 7}, "tool_calls": 6, "wall_seconds": 60,
                "max_output_tokens": {"writer": 900, "agent": 500}},
    "history_turns": 6,
    "cache": {"enabled": False, "threshold": 0.95, "ttl_seconds": 86400},
    "verifier": {"llm_for_hard": True},
    "tool_failure_message": DEFAULT_TOOL_FAILURE_MESSAGE,
    "backend": {"connect_timeout": CONNECT_TIMEOUT_S, "read_timeout": READ_TIMEOUT_S, "retries": RETRIES,
                "total_budget": TOTAL_BUDGET_S, "stale_after_hours": STALE_AFTER_HOURS,
                "cache_keep_days": CACHE_KEEP_DAYS},
    "web": {"order": ["wikipedia", "tavily"], "cache_hours": 24},
    "embedding": {"provider": "ollama", "model": "nomic-embed-text"},
    "memory": {"fact_cap": 50, "summary_cap_chars": 600, "retention_days": 180},
    "limits": {"max_input_chars": 2000, "per_user_per_10min": 20, "global_per_day": 2000},
    "output_format": ("Write short, clear paragraphs. Tables are allowed when comparing items. "
                      "(Placeholder: the final answer format is decided later.)"),
}


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    service_secret: str = ""
    admin_key: str = ""
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50052
    rest_host: str = "0.0.0.0"
    rest_port: int = 8001
    enable_rest: bool = True
    enable_embedding_admin: bool = False
    data_dir: str = "/data"
    knowledge_dir: str = "knowledge_base"
    curriculum_path: str = "curriculum/curriculum.yaml"
    glossary_path: str = "data/skills_glossary.txt"
    backend_base_url: str = "http://backend:8000"
    qdrant_url: str = ""
    ollama_base_url: str = "http://ollama:11434"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    embedding_provider: str = ""   # optional env override: ollama | gemini | fake
    embedding_model: str = ""
    use_fakes: bool = False        # run with fake providers (no keys needed) for smoke tests
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            service_secret=e("SERVICE_SECRET", ""), admin_key=e("ADMIN_KEY", ""),
            grpc_host=e("GRPC_HOST", "0.0.0.0"), grpc_port=int(e("GRPC_PORT", "50052")),
            rest_host=e("REST_HOST", "0.0.0.0"), rest_port=int(e("REST_PORT", "8001")),
            enable_rest=_bool("ENABLE_REST", True), enable_embedding_admin=_bool("ENABLE_EMBEDDING_ADMIN", False),
            data_dir=e("DATA_DIR", "/data"), knowledge_dir=e("KNOWLEDGE_DIR", "knowledge_base"),
            curriculum_path=e("CURRICULUM_PATH", "curriculum/curriculum.yaml"),
            glossary_path=e("GLOSSARY_PATH", "data/skills_glossary.txt"),
            backend_base_url=e("BACKEND_BASE_URL", "http://backend:8000"), qdrant_url=e("QDRANT_URL", ""),
            ollama_base_url=e("OLLAMA_BASE_URL", "http://ollama:11434"),
            embedding_provider=e("EMBEDDING_PROVIDER", ""), embedding_model=e("EMBEDDING_MODEL", ""),
            use_fakes=_bool("ADVISOR_USE_FAKES", False), log_level=e("LOG_LEVEL", "INFO"))

    def validate(self) -> list[str]:
        problems = []
        if not self.service_secret:
            problems.append("SERVICE_SECRET is not set")
        if not self.admin_key:
            problems.append("ADMIN_KEY is not set")
        if self.service_secret and self.service_secret == self.admin_key:
            problems.append("SERVICE_SECRET and ADMIN_KEY must be different")
        return problems
