"""Curriculum validation with readable errors: file, line, closest-match suggestions."""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass

from advisor.config.settings import CLUSTERS

from .loader import Curriculum, build_curriculum, find_curriculum_file, read_raw

SEMESTER_PATTERN = r"^\d\.\d$"       # ids like "3.1"; change here if the university uses another format
LEVELS = {"introduces", "practices", "advanced"}


@dataclass
class CIssue:
    file: str
    line: int | None
    level: str
    message: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"{where}: {self.level}: {self.message}"


def load_glossary(path: str) -> list[str]:
    """One canonical skill name per line (# comments allowed)."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]


def _suggest(value: str, options: list[str]) -> str:
    m = difflib.get_close_matches(value.lower(), [o.lower() for o in options], n=1, cutoff=0.6)
    if not m:
        return ""
    original = next(o for o in options if o.lower() == m[0])
    return f" Did you mean '{original}'?"


def validate_curriculum_data(data: dict, lines: dict, glossary: list[str], file: str = "curriculum.yaml",
                             semester_pattern: str = SEMESTER_PATTERN) -> list[CIssue]:
    issues: list[CIssue] = []

    def err(path: tuple, msg: str, level: str = "error") -> None:
        issues.append(CIssue(file, lines.get(path), level, msg))

    if not isinstance(data, dict) or not data:
        return [CIssue(file, None, "error", "file is empty or not a mapping")]
    if data.get("schema_version") != 1:
        err(("schema_version",), "schema_version must be 1")
    prog = data.get("program")
    if not isinstance(prog, dict) or not prog.get("id") or not prog.get("name"):
        err(("program",), "program needs 'id' and 'name'")
    elif "EXAMPLE" in str(prog.get("id")).upper():
        err(("program", "id"), "this is still the template: replace the EXAMPLE program and modules", "warning")
    sems = data.get("semesters")
    if not isinstance(sems, list) or not sems:
        err(("semesters",), "semesters must be a non-empty list")
        return issues
    gloss_lower = {g.lower() for g in glossary}
    seen_codes: dict[str, str] = {}
    seen_sems: set[str] = set()
    for si, sem in enumerate(sems):
        sp = ("semesters", si)
        sid = str((sem or {}).get("id", ""))
        if not re.match(semester_pattern, sid):
            err(sp + ("id",), f"semester id '{sid}' does not match the pattern {semester_pattern} (example: 3.1)")
        if sid in seen_sems:
            err(sp + ("id",), f"duplicate semester id '{sid}'")
        seen_sems.add(sid)
        mods = (sem or {}).get("modules")
        if not mods:
            err(sp, f"semester '{sid}' has no modules")
            continue
        for mi, m in enumerate(mods):
            mp = sp + ("modules", mi)
            code = str(m.get("code", "")).strip()
            if not code:
                err(mp, "module has no code")
            elif code in seen_codes:
                err(mp + ("code",), f"duplicate module code '{code}' (first used in semester {seen_codes[code]})")
            seen_codes[code] = sid
            if not m.get("name"):
                err(mp, f"module {code or '?'} has no name")
            if not isinstance(m.get("credits"), (int, float)) or m.get("credits", 0) < 0:
                err(mp + ("credits",), f"module {code}: credits must be a number")
            if m.get("level") not in LEVELS:
                err(mp + ("level",), f"module {code}: level '{m.get('level')}' must be one of {sorted(LEVELS)}")
            if not isinstance(m.get("optional", False), bool):
                err(mp + ("optional",), f"module {code}: optional must be true or false")
            for key in ("topics", "skills", "clusters"):
                if not isinstance(m.get(key, []), list):
                    err(mp + (key,), f"module {code}: {key} must be a list")
            for j, sk in enumerate(m.get("skills") or []):
                if glossary and str(sk).lower() not in gloss_lower:
                    err(mp + ("skills", j), f"module {code}: unknown skill '{sk}'.{_suggest(str(sk), glossary)}")
            for j, cl in enumerate(m.get("clusters") or []):
                if cl not in CLUSTERS:
                    err(mp + ("clusters", j), f"module {code}: unknown cluster '{cl}'.{_suggest(str(cl), CLUSTERS)}")
    if not glossary:
        issues.append(CIssue(file, None, "warning", "skills glossary not found: skill names were not checked"))
    return issues


@dataclass
class CurriculumState:
    status: str                   # ok | unavailable
    reason: str
    curriculum: Curriculum | None
    issues: list[CIssue]


def load_curriculum(path: str, glossary_path: str) -> CurriculumState:
    """Never raises: an invalid file makes the curriculum tools 'unavailable' with a reason."""
    real = find_curriculum_file(path)
    if not real:
        return CurriculumState("unavailable", f"curriculum file not found at {path}", None, [])
    try:
        data, lines = read_raw(real)
    except Exception as e:
        return CurriculumState("unavailable", f"curriculum file could not be read: {e}", None,
                               [CIssue(real, None, "error", str(e))])
    issues = validate_curriculum_data(data, lines, load_glossary(glossary_path), real)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        return CurriculumState("unavailable", f"curriculum file has {len(errors)} error(s); first: {errors[0]}",
                               None, issues)
    return CurriculumState("ok", "", build_curriculum(data), issues)
