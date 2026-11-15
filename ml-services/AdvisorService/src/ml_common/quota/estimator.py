"""Quota tracker: provider-reported numbers beat our own counts; answers-left estimate."""
from __future__ import annotations

import time
from typing import Callable

from .headers import Remaining, parse_ratelimit_headers
from .ledger import QuotaLedger


def answers_left(requests_left: int | None, tokens_left: int | None, avg_tokens: float | None) -> int | None:
    """min(requests_left, tokens_left / avg_tokens_per_answer). None if it cannot be computed honestly."""
    options = []
    if requests_left is not None:
        options.append(requests_left)
    if tokens_left is not None:
        if not avg_tokens:
            return None  # never guess an average
        options.append(int(tokens_left // avg_tokens))
    return min(options) if options else None


class QuotaTracker:
    def __init__(self, ledger: QuotaLedger, limits: dict[str, dict[str, int]] | None = None,
                 low_request_threshold: int = 3, clock: Callable[[], float] = time.time):
        self.ledger = ledger
        self.limits = limits or {}
        self.low_request_threshold = low_request_threshold
        self.clock = clock
        self.reported: dict[str, tuple[float, Remaining]] = {}

    def observe_headers(self, provider: str, headers: dict[str, str] | None) -> None:
        if not headers:
            return
        r = parse_ratelimit_headers(headers)
        if r.any():
            self.reported[provider] = (self.clock(), r)

    def _fresh(self, provider: str) -> Remaining | None:
        item = self.reported.get(provider)
        if not item:
            return None
        ts, r = item
        # reported numbers go stale once their reset time passed
        reset = max(r.reset_tokens_s or 0, r.reset_requests_s or 0)
        if reset and self.clock() - ts > reset:
            return None
        return r

    def is_low(self, provider: str, est_tokens: int = 0) -> bool:
        lim = self.limits.get(provider, {})
        r = self._fresh(provider)
        if r:
            if r.requests_remaining is not None and r.requests_remaining < self.low_request_threshold:
                return True
            if r.tokens_remaining is not None and r.tokens_remaining < est_tokens:
                return True
        tpm = lim.get("tpm")
        if tpm and self.ledger.tokens_last_minute(provider) + est_tokens > tpm:
            return True
        rpd = lim.get("rpd")
        if rpd and self.ledger.window(provider, 86400)["requests"] >= rpd:
            return True
        rpm = lim.get("rpm")
        if rpm and self.ledger.window(provider, 60)["requests"] >= rpm:
            return True
        return False

    def report(self, providers: list[str]) -> dict:
        avg_all = self.ledger.avg_tokens_per_answer()
        out = {}
        for p in providers:
            r = self._fresh(p)
            lim = self.limits.get(p, {})
            used = self.ledger.per_period(p)
            req_left = r.requests_remaining if r and r.requests_remaining is not None else (
                lim["rpd"] - used["day"]["requests"] if lim.get("rpd") else None)
            tok_left = r.tokens_remaining if r and r.tokens_remaining is not None else (
                lim["tpm"] - used["minute"]["tokens"] if lim.get("tpm") else None)
            avg = self.ledger.avg_tokens_per_answer(p) or avg_all
            out[p] = {"source": "provider" if r else ("own_count" if lim else "unknown"),
                      "requests_left": req_left, "tokens_left": tok_left,
                      "avg_tokens_per_answer": round(avg, 1) if avg else None,
                      "answers_left_estimate": answers_left(req_left, tok_left, avg), "used": used,
                      "known_limits": lim}
        return out
