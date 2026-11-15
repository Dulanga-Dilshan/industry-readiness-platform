"""Rule-based answer checks: unsupported numbers, invalid citation markers, secret leaks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from advisor.safety.guardrails import scrub_output

SMALL_INT_MAX = 10          # list numbers, "3 steps", months: always allowed
NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*%?")
MARKER = re.compile(r"\[S(\d+)\]")


def numbers_in(text: str) -> set[float]:
    out = set()
    for m in NUM.finditer(text):
        whole = m.group(1).replace(",", "")
        out.add(float(f"{whole}.{m.group(2)}" if m.group(2) else whole))
    return out


@dataclass
class Verdict:
    text: str
    unsupported_numbers: list[float] = field(default_factory=list)
    bad_markers: list[str] = field(default_factory=list)
    leaked: bool = False

    @property
    def needs_rewrite(self) -> bool:
        return bool(self.unsupported_numbers) or self.leaked


def check_answer(answer: str, evidence_text: str, question: str, valid_markers: set[str],
                 secrets_list: list[str] | None = None) -> Verdict:
    allowed = numbers_in(evidence_text) | numbers_in(question)
    bad = []
    for n in sorted(numbers_in(MARKER.sub("", answer))):
        if n in allowed or (float(n).is_integer() and (n <= SMALL_INT_MAX or 1990 <= n <= 2100)):
            continue
        bad.append(n)
    text = answer
    bad_markers = [m for m in MARKER.findall(answer) if f"S{m}" not in valid_markers]
    for m in bad_markers:
        text = text.replace(f"[S{m}]", "")
    text, leaked = scrub_output(text, secrets_list)
    return Verdict(text, bad, [f"S{m}" for m in bad_markers], leaked)


def rewrite_instruction(v: Verdict) -> str:
    parts = []
    if v.unsupported_numbers:
        nums = ", ".join(str(int(n)) if n.is_integer() else str(n) for n in v.unsupported_numbers)
        parts.append(f"Your previous draft used numbers that are not in the evidence ({nums}). "
                     "Rewrite the answer using only numbers that appear in the evidence, or say the number is not available.")
    if v.leaked:
        parts.append("Your previous draft contained something that looks like a secret. Remove it.")
    return " ".join(parts)
