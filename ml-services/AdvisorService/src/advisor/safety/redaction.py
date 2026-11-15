"""Redaction of personal data in logs and web queries."""
from __future__ import annotations

import logging
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d")


def redact(text: str) -> str:
    return PHONE.sub("[phone]", EMAIL.sub("[email]", text))


def profile_values(profile: dict | None) -> list[str]:
    """Free-text profile values that must never reach a web query."""
    if not profile:
        return []
    vals: list[str] = []
    for key in ("goals", "constraints", "self_assessed_skills"):
        vals += [str(v) for v in profile.get(key, []) or []]
    vals += [str(f.get("text", "")) for f in profile.get("facts", []) or []]
    vals += [str(s.get("text", "")) for s in profile.get("summaries", []) or []]
    vals.append(str(profile.get("user_id", "")))
    return [v.strip().lower() for v in vals if len(v.strip()) >= 6]


def query_leaks_profile(query: str, profile: dict | None) -> bool:
    q = query.lower()
    if EMAIL.search(q) or PHONE.search(q):
        return True
    return any(v in q for v in profile_values(profile))


def safe_profile_summary(profile: dict | None) -> dict:
    """What may appear in logs: structure only, no content."""
    if not profile:
        return {}
    return {"has_profile": True, "facts": len(profile.get("facts", []) or []),
            "semester": profile.get("current_semester", ""), "cluster": profile.get("target_cluster", "")}


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def install_log_redaction() -> None:
    f = RedactingFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(f)
