"""Load markdown knowledge files with YAML front matter."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

import yaml

REQUIRED_KEYS = ["id", "title", "type", "cluster", "status", "last_reviewed"]
TYPES = {"framework", "guide", "tool", "recruiter", "faq"}
STATUSES = {"draft", "approved"}


@dataclass
class KBFile:
    path: str
    rel: str
    meta: dict
    body: str
    body_line_offset: int
    hash: str
    problems: list[tuple[int, str]] = field(default_factory=list)   # (line, message)

    @property
    def approved(self) -> bool:
        return self.meta.get("status") == "approved"

    @property
    def id(self) -> str:
        return str(self.meta.get("id") or self.rel)


def parse_front_matter(text: str) -> tuple[dict, str, int, list[tuple[int, str]]]:
    """Returns (meta, body, body_line_offset, problems)."""
    problems: list[tuple[int, str]] = []
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text, 0, [(1, "front matter missing (file must start with --- ... ---)")]
    try:
        meta = yaml.safe_load(m.group(1)) or {}
        if not isinstance(meta, dict):
            raise yaml.YAMLError("front matter is not a mapping")
    except yaml.YAMLError as e:
        line = getattr(getattr(e, "problem_mark", None), "line", 0) + 2
        return {}, text[m.end():], text[:m.end()].count("\n"), [(line, f"front matter is not valid YAML: {e}")]
    return meta, text[m.end():], text[:m.end()].count("\n"), problems


def load_kb(directory: str) -> list[KBFile]:
    files: list[KBFile] = []
    if not os.path.isdir(directory):
        return files
    for root, _, names in os.walk(directory):
        for n in sorted(names):
            if not n.endswith(".md") or n.upper().startswith("README"):
                continue
            path = os.path.join(root, n)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            meta, body, offset, problems = parse_front_matter(text)
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            files.append(KBFile(path, os.path.relpath(path, directory).replace("\\", "/"), meta, body, offset, h, problems))
    files.sort(key=lambda f: f.rel)
    return files
