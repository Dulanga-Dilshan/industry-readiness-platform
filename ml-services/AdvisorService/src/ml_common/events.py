"""Event model shared by every service (same set in gRPC and SSE)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS = "status"
CORRECTED = "corrected"
REROUTING = "rerouting"
RESET = "reset"
TOKEN = "token"
SOURCE = "source"
TOOL = "tool"
NOTE = "note"
DONE = "done"
ERROR = "error"
# internal events (never sent to the student)
TOOL_CALLS = "tool_calls"
USAGE = "usage"
MODEL = "model"
THINKING = "thinking"

PUBLIC_TYPES = {STATUS, CORRECTED, REROUTING, RESET, TOKEN, SOURCE, TOOL, NOTE, DONE, ERROR}


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}

    @property
    def public(self) -> bool:
        return self.type in PUBLIC_TYPES


def status(text: str) -> Event:
    return Event(STATUS, {"text": text})


def token(text: str) -> Event:
    return Event(TOKEN, {"text": text})


def note(text: str, code: str = "") -> Event:
    return Event(NOTE, {"text": text, "code": code})


def error(code: str, message: str, retryable: bool = False) -> Event:
    return Event(ERROR, {"code": code, "message": message, "retryable": retryable})
