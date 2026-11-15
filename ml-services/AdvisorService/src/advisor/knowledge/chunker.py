"""Split a knowledge file at second-level headings. Each section should be self-contained."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .loader import KBFile

MAX_CHARS = 1500


@dataclass
class Chunk:
    id: str
    text: str            # what is embedded and shown to the model
    payload: dict


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > MAX_CHARS:
            parts.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _useful(body: str) -> bool:
    stripped = re.sub(r"(?im)^\s*(?:[-*]\s*)?(?:todo|tbd)\b.*$", "", body).strip()
    return len(stripped) >= 20


def chunk_file(f: KBFile) -> list[Chunk]:
    title = str(f.meta.get("title", f.rel))
    sections: list[tuple[str, str]] = []
    heading, buf = "Overview", []
    for line in f.body.splitlines():
        if line.startswith("## "):
            sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    sections.append((heading, "\n".join(buf).strip()))
    chunks: list[Chunk] = []
    n = 0
    for heading, body in sections:
        if not _useful(body):
            continue
        for part in _split_long(body):
            cid = f"{f.id}#{n}"
            n += 1
            text = f"{title} — {heading}\n{part}"
            chunks.append(Chunk(cid, text, {
                "file_id": f.id, "file": f.rel, "file_hash": f.hash, "title": title, "heading": heading,
                "type": f.meta.get("type", ""), "cluster": f.meta.get("cluster", ""), "text": text,
                "ref": f"{f.rel}#{heading}"}))
    return chunks
