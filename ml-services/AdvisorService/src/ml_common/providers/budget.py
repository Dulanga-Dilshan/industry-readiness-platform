"""Token budgeting. Trim order: older history, lowest-score chunks, tool rows, profile facts.
The system prompt and the question are never trimmed."""
from __future__ import annotations

from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


@dataclass
class Block:
    """Evidence placed in the user message, before the question (stable prefix first)."""
    kind: str                      # chunk | tool | profile
    label: str
    text: str = ""
    score: float = 1.0
    rows: list[str] = field(default_factory=list)   # tool rows or profile lines

    def render(self) -> str:
        body = "\n".join(self.rows) if self.rows else self.text
        return f"{self.label}\n{body}" if self.label else body

    def tokens(self) -> int:
        return estimate_tokens(self.render())


@dataclass
class Prompt:
    system: str
    question: str
    history: list[dict] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    extra: list[dict] = field(default_factory=list)   # tool-loop messages, never trimmed
    trimmed: list[str] = field(default_factory=list)

    def _messages(self, history, blocks) -> list[dict]:
        parts = [b.render() for b in blocks if b.render().strip()]
        user = "\n\n".join(parts + [self.question]) if parts else self.question
        return [{"role": "system", "content": self.system}, *history,
                {"role": "user", "content": user}, *self.extra]

    @staticmethod
    def _count(messages: list[dict]) -> int:
        total = 0
        for m in messages:
            total += estimate_tokens(str(m.get("content") or "")) + 4
            if m.get("tool_calls"):
                total += estimate_tokens(str(m["tool_calls"]))
        return total

    def render(self, limit: int | None = None) -> list[dict]:
        history = list(self.history)
        blocks = [Block(b.kind, b.label, b.text, b.score, list(b.rows)) for b in self.blocks]
        self.trimmed = []
        if limit is None:
            return self._messages(history, blocks)

        def over() -> bool:
            return self._count(self._messages(history, blocks)) > limit

        # 1. older history, oldest first
        while over() and history:
            history.pop(0)
            self._note("history")
        # 2. lowest-score retrieved chunks
        while over():
            chunks = [b for b in blocks if b.kind == "chunk"]
            if not chunks:
                break
            blocks.remove(min(chunks, key=lambda b: b.score))
            self._note("chunks")
        # 3. tool result rows, from the longest block, last row first
        while over():
            tools = [b for b in blocks if b.kind == "tool" and len(b.rows) > 1]
            if not tools:
                break
            max(tools, key=lambda b: len(b.rows)).rows.pop()
            self._note("tool_rows")
        # 4. profile facts, last first
        while over():
            prof = [b for b in blocks if b.kind == "profile" and b.rows]
            if not prof:
                break
            prof[-1].rows.pop()
            self._note("profile")
        return self._messages(history, blocks)

    def _note(self, what: str) -> None:
        if what not in self.trimmed:
            self.trimmed.append(what)
