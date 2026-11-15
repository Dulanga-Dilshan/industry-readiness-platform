"""Tool result envelope: {status, reason, as_of, data}. The model never sees raw exceptions."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

STATUSES = ("ok", "stale", "empty", "unavailable", "error")


@dataclass
class ToolResult:
    status: str = "ok"
    reason: str = ""
    as_of: str | None = None
    data: Any = None

    @classmethod
    def ok(cls, data: Any, as_of: str | None = None) -> "ToolResult":
        return cls("ok", "", as_of, data)

    @classmethod
    def empty(cls, reason: str = "no results", as_of: str | None = None) -> "ToolResult":
        return cls("empty", reason, as_of, None)

    @classmethod
    def unavailable(cls, reason: str) -> "ToolResult":
        return cls("unavailable", reason, None, None)

    @classmethod
    def error(cls, reason: str) -> "ToolResult":
        return cls("error", reason, None, None)

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "stale")

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "as_of": self.as_of, "data": self.data}

    def to_json(self, max_chars: int = 6000) -> str:
        s = json.dumps(self.to_dict(), ensure_ascii=False, default=str)
        if len(s) <= max_chars:
            return s
        return s[:max_chars] + '..."truncated"'
