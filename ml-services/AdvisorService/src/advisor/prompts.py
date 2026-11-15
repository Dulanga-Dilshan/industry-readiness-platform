"""Prompt files are versioned (<!-- version: N --> on the first line)."""
from __future__ import annotations

import os
import re

DIR = os.path.join(os.path.dirname(__file__), "agents", "prompts")


def load_prompt(name: str, **vars: str) -> str:
    with open(os.path.join(DIR, f"{name}.md"), "r", encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"^<!--.*?-->\s*", "", text, count=1, flags=re.S)
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", v)
    return text.strip()


def prompt_version(name: str) -> int:
    with open(os.path.join(DIR, f"{name}.md"), "r", encoding="utf-8") as f:
        m = re.search(r"version:\s*(\d+)", f.readline())
    return int(m.group(1)) if m else 0


def all_versions() -> dict[str, int]:
    return {n[:-3]: prompt_version(n[:-3]) for n in sorted(os.listdir(DIR)) if n.endswith(".md")}
