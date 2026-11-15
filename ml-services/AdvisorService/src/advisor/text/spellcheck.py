"""Deterministic spelling correction (no LLM, no tokens). Protects technical terms."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from spellchecker import SpellChecker

TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]*|\S")
SKIP_PATTERNS = [re.compile(p) for p in (r"https?://\S+", r"\S+@\S+\.\S+", r"`[^`]*`")]
MIN_LEN = 4
MIN_FREQ_RATIO = 5.0       # best candidate must be this much more frequent than the runner-up
MANY_SUSPICIOUS = 0.34     # share of unknown, uncorrected words that asks for an LLM rewrite


@dataclass
class SpellResult:
    original: str
    corrected: str
    changes: list[tuple[str, str]] = field(default_factory=list)
    needs_rewrite: bool = False

    @property
    def changed(self) -> bool:
        return self.original != self.corrected


def _is_codeish(tok: str) -> bool:
    return (any(c.isdigit() for c in tok) or "_" in tok or "." in tok or "/" in tok
            or (tok != tok.lower() and tok != tok.upper() and tok[1:] != tok[1:].lower())   # camelCase / PascalCase mid caps
            or (tok.isupper() and len(tok) > 1))                                             # acronyms: CCNA, SQL, AWS


def _damerau1(a: str, b: str) -> bool:
    """True when a and b differ by one insertion, deletion, substitution or adjacent swap."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    s, l = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(s) and s[i] == l[i]:
        i += 1
    return s[i:] == l[i + 1:]


class SpellCorrector:
    def __init__(self, glossary: list[str] | None = None):
        self.spell = SpellChecker(distance=1)
        self.glossary = {g.lower() for g in (glossary or [])}
        self.glossary_words: set[str] = set()
        self.add_terms(glossary or [])

    def add_terms(self, terms) -> None:
        """Protected terms: skills, aliases, cluster names, tools, KB titles, module names."""
        for t in terms:
            t = str(t)
            self.glossary.add(t.lower())
            for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", t):
                self.glossary_words.add(w.lower())

    def _protected(self, low: str) -> bool:
        return low in self.glossary or low in self.glossary_words

    def _fix(self, word: str) -> str | None:
        low = word.lower()
        if len(low) < MIN_LEN or self._protected(low) or low in self.spell:
            return None
        # prefer a protected technical term one edit away (Phyton -> Python)
        near = [g for g in self.glossary_words if abs(len(g) - len(low)) <= 1 and _damerau1(low, g)]
        if len(near) == 1:
            return near[0]
        cands = self.spell.candidates(low) or set()
        cands = {c for c in cands if c != low and _damerau1(low, c)}
        if not cands:
            return None
        ranked = sorted(cands, key=lambda c: -self.spell.word_usage_frequency(c))
        if len(ranked) > 1:
            top, second = (self.spell.word_usage_frequency(ranked[0]), self.spell.word_usage_frequency(ranked[1]))
            if second > 0 and top / second < MIN_FREQ_RATIO:
                return None          # ambiguous: leave it alone
        return ranked[0]

    def correct(self, text: str) -> SpellResult:
        protected_spans = []
        for pat in SKIP_PATTERNS:
            protected_spans += [(m.start(), m.end()) for m in pat.finditer(text)]

        def in_span(i: int) -> bool:
            return any(s <= i < e for s, e in protected_spans)

        out, changes, unknown, words = [], [], 0, 0
        pos = 0
        for m in TOKEN.finditer(text):
            out.append(text[pos:m.start()])
            pos = m.end()
            tok = m.group()
            if not tok[0].isalpha() or in_span(m.start()) or "'" in tok:
                out.append(tok)
                continue
            words += 1
            if _is_codeish(tok):
                out.append(tok)
                continue
            parts = tok.split("-")
            new_parts = []
            for p in parts:
                fixed = self._fix(p) if p else None
                if fixed:
                    if p[0].isupper():
                        fixed = fixed.capitalize() if not p.isupper() else fixed.upper()
                    changes.append((p, fixed))
                    new_parts.append(fixed)
                else:
                    if p and len(p) >= MIN_LEN and p.lower() not in self.spell and not self._protected(p.lower()):
                        unknown += 1
                    new_parts.append(p)
            out.append("-".join(new_parts))
        out.append(text[pos:])
        corrected = "".join(out)
        return SpellResult(text, corrected, changes, needs_rewrite=words > 0 and unknown / words >= MANY_SUSPICIOUS)


def normalize(text: str) -> str:
    """Whitespace normalisation applied before spelling."""
    return re.sub(r"\s+", " ", text.replace("\u200b", "")).strip()
