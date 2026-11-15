"""Cooldowns and disabled state per provider."""
from __future__ import annotations

import time
from typing import Callable

COOLDOWNS = {"rate_limit": 30.0, "server": 60.0, "timeout": 30.0}
DISABLING = {"auth", "credit"}


class HealthTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic, cooldowns: dict[str, float] | None = None):
        self.clock = clock
        self.cooldowns = {**COOLDOWNS, **(cooldowns or {})}
        self._until: dict[str, float] = {}
        self._reason: dict[str, str] = {}
        self.disabled: dict[str, str] = {}
        self.failures: dict[str, int] = {}

    def mark_failure(self, name: str, kind: str, retry_after: float | None = None) -> None:
        self.failures[name] = self.failures.get(name, 0) + 1
        if kind in DISABLING:
            self.disabled[name] = kind          # stays off until manually re-enabled
            return
        seconds = retry_after if (kind == "rate_limit" and retry_after) else self.cooldowns.get(kind, 0.0)
        if seconds > 0:
            self._until[name] = self.clock() + seconds
            self._reason[name] = kind

    def mark_success(self, name: str) -> None:
        self.failures[name] = 0
        self._until.pop(name, None)
        self._reason.pop(name, None)

    def cooldown_left(self, name: str) -> float:
        left = self._until.get(name, 0.0) - self.clock()
        if left <= 0:
            self._until.pop(name, None)
            self._reason.pop(name, None)
            return 0.0
        return left

    def available(self, name: str) -> bool:
        return name not in self.disabled and self.cooldown_left(name) == 0.0

    def reenable(self, name: str) -> None:
        self.disabled.pop(name, None)
        self._until.pop(name, None)
        self.failures[name] = 0

    def state(self, name: str) -> dict:
        return {"cooling_down": self.cooldown_left(name) > 0, "cooldown_seconds": round(self.cooldown_left(name), 1),
                "cooldown_reason": self._reason.get(name, ""), "disabled_reason": self.disabled.get(name, ""),
                "consecutive_failures": self.failures.get(name, 0)}
