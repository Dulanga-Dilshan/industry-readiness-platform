"""Runtime configuration persisted as JSON on the advisor volume. Never holds secrets."""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

from advisor.config.settings import DEFAULT_RUNTIME

SECRET_HINTS = ("key", "secret", "token", "password")


class ConfigError(ValueError):
    pass


def _merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k not in ("tiers", "fallback", "providers"):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _reject_secrets(patch: Any, path: str = "") -> None:
    if isinstance(patch, dict):
        for k, v in patch.items():
            if any(h in k.lower() for h in SECRET_HINTS) and k != "api_key_env":
                raise ConfigError(f"secrets are not accepted in runtime config ({path}{k})")
            _reject_secrets(v, f"{path}{k}.")


class RuntimeConfig:
    def __init__(self, path: str | None = None, known_providers: set[str] | None = None):
        self.path = path
        self._lock = threading.Lock()
        self.known_providers = known_providers or set()
        self.data: dict = copy.deepcopy(DEFAULT_RUNTIME)
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _merge(self.data, json.load(f))
            except (OSError, json.JSONDecodeError):
                pass   # a broken file never stops the service; defaults apply

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def snapshot(self) -> dict:
        return copy.deepcopy(self.data)

    def _validate(self, data: dict) -> None:
        for tier, names in data["tiers"].items():
            if not isinstance(names, list):
                raise ConfigError(f"tier {tier} must be a list")
            for n in names:
                if self.known_providers and n not in self.known_providers:
                    raise ConfigError(f"unknown provider '{n}' in tier '{tier}'")
        for name in data["providers"]:
            if self.known_providers and name not in self.known_providers:
                raise ConfigError(f"unknown provider '{name}'")
        budgets = data["budgets"]["llm_calls"]
        if not all(isinstance(budgets.get(k), int) and budgets[k] > 0 for k in ("easy", "medium", "hard")):
            raise ConfigError("llm_calls budgets must be positive integers")
        c = data["cache"]
        if not 0.5 <= float(c["threshold"]) <= 1.0:
            raise ConfigError("cache threshold must be between 0.5 and 1.0")

    def update(self, patch: dict) -> dict:
        _reject_secrets(patch)
        with self._lock:
            new = _merge(copy.deepcopy(self.data), copy.deepcopy(patch))
            self._validate(new)
            self.data = new
            self._save()
            return self.snapshot()

    def set_provider(self, name: str, **overrides: Any) -> dict:
        allowed = {"enabled", "personal_data_ok", "tools_ok"}
        bad = set(overrides) - allowed
        if bad:
            raise ConfigError(f"cannot change: {sorted(bad)}")
        with self._lock:
            new = copy.deepcopy(self.data)
            new["providers"].setdefault(name, {}).update({k: v for k, v in overrides.items() if v is not None})
            self._validate(new)
            self.data = new
            self._save()
            return self.snapshot()

    def _save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
