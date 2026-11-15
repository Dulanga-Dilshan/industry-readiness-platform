"""In-process BM25 over knowledge chunks (small corpus, built at startup)."""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

TOKEN = re.compile(r"[a-z0-9+#.]+")


STOPWORDS = set("a an and are as at be by can do does for from has have how i in is it its me my of on or so that the "
                "their then there these this to was we what when where which who why will with you your about tell "
                "explain should would could".split())


def tokenize(text: str) -> list[str]:
    return [t for t in (x.strip(".") for x in TOKEN.findall(text.lower())) if t and t not in STOPWORDS]


class KeywordIndex:
    def __init__(self, chunks: list[tuple[str, str]] | None = None):
        self.ids: list[str] = []
        self.bm25: BM25Okapi | None = None
        if chunks:
            self.build(chunks)

    def build(self, chunks: list[tuple[str, str]]) -> None:
        self.ids = [c[0] for c in chunks]
        docs = [tokenize(c[1]) or ["_"] for c in chunks]
        self.bm25 = BM25Okapi(docs) if docs else None

    def search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        toks = tokenize(query)
        if not self.bm25 or not toks:
            return []
        scores = self.bm25.get_scores(toks)
        ranked = sorted(zip(self.ids, scores), key=lambda x: -x[1])
        return [(i, float(s)) for i, s in ranked[:limit] if s > 0]
