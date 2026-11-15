"""Input screening, rate limits, untrusted-content wrapping and output scrubbing."""
from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

REFUSAL = ("I can only help with IT careers, skills, learning paths and the job market. "
           "I can't help with that request.")
INJECTION = [re.compile(p, re.I) for p in (
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any )?(previous|prior|above|your) (instructions|prompts?|rules)",
    r"(reveal|show|print|repeat|tell me) (me )?(your|the) (system )?(prompt|instructions)",
    r"(show|print|give|reveal|tell me) (me )?(the |your )?(api[ _-]?keys?|secrets?|passwords?|tokens?)",
    r"you are now (dan|in developer mode|unrestricted)",
    r"pretend (you have|there are) no (rules|restrictions)",
)]
LEAK = [re.compile(p) for p in (r"\bsk-[A-Za-z0-9_-]{16,}", r"\bgsk_[A-Za-z0-9]{16,}", r"\bAIza[0-9A-Za-z_-]{20,}",
                                r"\bBearer\s+[A-Za-z0-9._-]{20,}", r"\btvly-[A-Za-z0-9_-]{10,}")]


@dataclass
class Screen:
    ok: bool
    reason: str = ""
    message: str = ""


def screen_input(text: str, max_chars: int = 2000) -> Screen:
    if not text or not text.strip():
        return Screen(False, "empty", "Please type a question.")
    if len(text) > max_chars:
        return Screen(False, "too_long", f"Your message is too long. Please keep it under {max_chars} characters.")
    if any(p.search(text) for p in INJECTION):
        return Screen(False, "injection", REFUSAL)
    return Screen(True)


def scrub_output(text: str, secrets_list: list[str] | None = None) -> tuple[str, bool]:
    """Returns (text, leaked). Replaces anything that looks like a key or one of our secrets."""
    leaked = False
    for p in LEAK:
        if p.search(text):
            leaked = True
            text = p.sub("[removed]", text)
    for s in secrets_list or []:
        if s and len(s) >= 6 and s in text:
            leaked = True
            text = text.replace(s, "[removed]")
    return text, leaked


def wrap_untrusted(kind: str, source: str, text: str) -> str:
    """Delimit retrieved text so the model treats it as data. A random nonce stops text from closing the block."""
    nonce = secrets.token_hex(4)
    text = re.sub(r"<\s*/?\s*untrusted[^>]*>", "", text, flags=re.I)
    return f'<untrusted id="{nonce}" kind="{kind}" source="{source}">\n{text}\n</untrusted id="{nonce}">'


class RateLimiter:
    """Per-user sliding window plus a global daily counter."""

    def __init__(self, per_user: int, window_s: float = 600.0, global_per_day: int = 2000,
                 clock: Callable[[], float] = time.time):
        self.per_user, self.window_s, self.global_per_day, self.clock = per_user, window_s, global_per_day, clock
        self.hits: dict[str, deque] = defaultdict(deque)
        self.day_hits: deque = deque()

    def check(self, user_id: str) -> tuple[bool, str]:
        now = self.clock()
        while self.day_hits and now - self.day_hits[0] > 86400:
            self.day_hits.popleft()
        q = self.hits[user_id or "anon"]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(self.day_hits) >= self.global_per_day:
            return False, "The advisor has reached its daily limit. Please try again tomorrow."
        if len(q) >= self.per_user:
            return False, "You are asking questions very quickly. Please wait a few minutes and try again."
        q.append(now)
        self.day_hits.append(now)
        return True, ""
