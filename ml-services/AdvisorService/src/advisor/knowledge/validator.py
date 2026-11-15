"""Readable validation errors for the knowledge base (file and line)."""
from __future__ import annotations

from dataclasses import dataclass

from advisor.config.settings import CLUSTERS

from .loader import REQUIRED_KEYS, STATUSES, TYPES, KBFile


@dataclass
class Issue:
    file: str
    line: int
    level: str       # error | warning
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.level}: {self.message}"


def validate_kb(files: list[KBFile]) -> list[Issue]:
    issues: list[Issue] = []
    seen: dict[str, str] = {}
    valid_clusters = set(CLUSTERS) | {"all", "core"}
    for f in files:
        for line, msg in f.problems:
            issues.append(Issue(f.rel, line, "error", msg))
        if f.problems and not f.meta:
            continue
        for k in REQUIRED_KEYS:
            if not f.meta.get(k):
                issues.append(Issue(f.rel, 1, "error", f"front matter key '{k}' is missing"))
        if f.meta.get("type") and f.meta["type"] not in TYPES:
            issues.append(Issue(f.rel, 1, "error", f"type '{f.meta['type']}' must be one of {sorted(TYPES)}"))
        if f.meta.get("status") and f.meta["status"] not in STATUSES:
            issues.append(Issue(f.rel, 1, "error", f"status '{f.meta['status']}' must be draft or approved"))
        if f.meta.get("cluster") and f.meta["cluster"] not in valid_clusters:
            issues.append(Issue(f.rel, 1, "error", f"cluster '{f.meta['cluster']}' is not one of {sorted(valid_clusters)}"))
        fid = str(f.meta.get("id", ""))
        if fid:
            if fid in seen:
                issues.append(Issue(f.rel, 1, "error", f"duplicate id '{fid}' (also in {seen[fid]})"))
            seen[fid] = f.rel
        if f.approved and "TODO" in f.body:
            issues.append(Issue(f.rel, 1, "warning", "approved file still contains TODO notes"))
        if f.approved and len(f.body.strip()) < 40:
            issues.append(Issue(f.rel, 1, "warning", "approved file has almost no content"))
    return issues
