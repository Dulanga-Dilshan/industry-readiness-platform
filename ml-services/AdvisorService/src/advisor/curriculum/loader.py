"""Load curriculum.yaml (or .json) with line numbers for readable validation errors."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Module:
    code: str
    name: str
    credits: float
    topics: list[str]
    skills: list[str]
    clusters: list[str]
    level: str
    optional: bool = False


@dataclass
class Semester:
    id: str
    modules: list[Module] = field(default_factory=list)


@dataclass
class Curriculum:
    program_id: str
    program_name: str
    version: str
    semesters: list[Semester]

    def index_of(self, semester_id: str) -> int | None:
        for i, s in enumerate(self.semesters):
            if s.id == semester_id:
                return i
        return None


def _walk(node, path: tuple, out: dict) -> None:
    """Record the 1-based line of every mapping key and sequence item."""
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            p = path + (k.value,)
            out[p] = k.start_mark.line + 1
            _walk(v, p, out)
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            p = path + (i,)
            out[p] = item.start_mark.line + 1
            _walk(item, p, out)


def read_raw(path: str) -> tuple[dict, dict]:
    """Returns (data, lines). lines maps a path tuple to a line number (YAML only)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.lower().endswith(".json"):
        return json.loads(text), {}
    node = yaml.compose(text)
    lines: dict = {}
    if node is not None:
        _walk(node, (), lines)
    data = yaml.safe_load(text)
    return (data if data is not None else {}), lines


def find_curriculum_file(path: str) -> str | None:
    if os.path.exists(path):
        return path
    base, _ = os.path.splitext(path)
    for ext in (".yaml", ".yml", ".json"):
        if os.path.exists(base + ext):
            return base + ext
    return None


def build_curriculum(data: dict) -> Curriculum:
    prog = data.get("program") or {}
    sems = []
    for s in data.get("semesters") or []:
        mods = [Module(code=str(m.get("code", "")), name=str(m.get("name", "")), credits=float(m.get("credits") or 0),
                       topics=list(m.get("topics") or []), skills=list(m.get("skills") or []),
                       clusters=list(m.get("clusters") or []), level=str(m.get("level", "")),
                       optional=bool(m.get("optional", False))) for m in (s.get("modules") or [])]
        sems.append(Semester(str(s.get("id", "")), mods))
    return Curriculum(str(prog.get("id", "")), str(prog.get("name", "")), str(prog.get("version", "")), sems)
