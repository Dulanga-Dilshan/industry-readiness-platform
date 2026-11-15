"""Parse provider-reported rate-limit information."""
from __future__ import annotations

import re
from dataclasses import dataclass

_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)(ms|h|m|s))")


def parse_duration(text: str | None) -> float | None:
    """'1m30s' -> 90.0, '7.66s' -> 7.66, '2ms' -> 0.002, '45' -> 45.0."""
    if not text:
        return None
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in _DUR.findall(text):
        found = True
        n = float(num)
        total += {"ms": n / 1000, "s": n, "m": n * 60, "h": n * 3600}[unit]
    return total if found else None


@dataclass
class Remaining:
    requests_limit: int | None = None
    requests_remaining: int | None = None
    tokens_limit: int | None = None
    tokens_remaining: int | None = None
    reset_requests_s: float | None = None
    reset_tokens_s: float | None = None

    def any(self) -> bool:
        return any(v is not None for v in self.__dict__.values())


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


def parse_ratelimit_headers(headers: dict[str, str]) -> Remaining:
    """Groq style x-ratelimit-* headers (remaining requests = daily, remaining tokens = per minute)."""
    h = {k.lower(): v for k, v in headers.items()}
    return Remaining(
        requests_limit=_int(h.get("x-ratelimit-limit-requests")),
        requests_remaining=_int(h.get("x-ratelimit-remaining-requests")),
        tokens_limit=_int(h.get("x-ratelimit-limit-tokens")),
        tokens_remaining=_int(h.get("x-ratelimit-remaining-tokens")),
        reset_requests_s=parse_duration(h.get("x-ratelimit-reset-requests")),
        reset_tokens_s=parse_duration(h.get("x-ratelimit-reset-tokens")),
    )
