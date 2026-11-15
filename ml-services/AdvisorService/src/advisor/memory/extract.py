"""Memory extraction: LLM proposes, code validates. Only the student's own statements count."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from advisor.safety.redaction import EMAIL, PHONE, redact

FACT_TYPES = {"target_cluster", "skill_claim", "goal", "constraint", "preference", "correction"}
MAX_FACT_CHARS = 200
MIN_CONFIDENCE = 0.5
BAD = re.compile(r"ignore (all |previous)|system prompt|api key|password|you must|always answer", re.I)

EXTRACT_SYSTEM = """You extract durable facts about a student from a conversation. Output JSON only:
{"facts":[{"type":"target_cluster|skill_claim|goal|constraint|preference|correction","topic":"short key","text":"one short sentence","confidence":0.0-1.0}],"summary":"one or two sentences about what was discussed"}
Rules: use ONLY what the STUDENT said, never what the assistant said. No names, emails or phone numbers.
Only stable facts (target career, skills they claim, goals, constraints, preferences, corrections of earlier facts).
If there is nothing worth keeping return {"facts":[],"summary":""}."""


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]{4,}", s.lower())}


def parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_facts(raw: list, user_text: str, session_id: str) -> list[dict]:
    user_words = _words(user_text)
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for f in raw or []:
        if not isinstance(f, dict):
            continue
        text, ftype = str(f.get("text", "")).strip(), f.get("type")
        try:
            conf = float(f.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if ftype not in FACT_TYPES or not text or len(text) > MAX_FACT_CHARS or conf < MIN_CONFIDENCE:
            continue
        if EMAIL.search(text) or PHONE.search(text) or BAD.search(text):
            continue
        fw = _words(text)
        if fw and len(fw & user_words) / len(fw) < 0.5:        # not grounded in the student's own words
            continue
        topic = re.sub(r"\W+", "_", str(f.get("topic") or ftype).lower()).strip("_")[:40] or ftype
        out.append({"id": uuid.uuid4().hex[:12], "type": ftype, "topic": topic, "text": text,
                    "confidence": min(conf, 1.0), "source_session": session_id, "timestamp": now})
    return out


def merge_facts(existing: list[dict], new: list[dict], cap: int = 50) -> list[dict]:
    """New facts replace older facts on the same topic (corrections win). Duplicates are dropped. Oldest trimmed past cap."""
    merged = {f["topic"]: f for f in existing}
    seen_text = {re.sub(r"\W+", " ", f["text"].lower()).strip(): f["topic"] for f in existing}
    for f in new:
        key = re.sub(r"\W+", " ", f["text"].lower()).strip()
        if key in seen_text and seen_text[key] in merged and f["type"] != "correction":
            continue
        merged[f["topic"]] = f
        seen_text[key] = f["topic"]
    facts = sorted(merged.values(), key=lambda f: f.get("timestamp", ""), reverse=True)
    return facts[:cap]


def clean_summary(text: str, cap: int) -> str:
    text = redact(str(text or "").strip())
    return text[:cap]
