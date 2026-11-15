"""Deterministic curriculum tools (code, not LLM)."""
from __future__ import annotations

from typing import Awaitable, Callable

from advisor.tools.envelope import ToolResult

from .loader import Curriculum
from .validator import CurriculumState

TopSkillsFn = Callable[[str, str, int], Awaitable[ToolResult]]


class CurriculumTools:
    def __init__(self, state: CurriculumState):
        self.state = state

    @property
    def available(self) -> bool:
        return self.state.status == "ok" and self.state.curriculum is not None

    def _check(self, semester: str | None) -> ToolResult | None:
        if not self.available:
            return ToolResult.unavailable(self.state.reason or "curriculum not available")
        if not semester:
            return ToolResult.error("semester is missing: ask the student which semester they are in")
        if self.state.curriculum.index_of(semester) is None:
            known = ", ".join(s.id for s in self.state.curriculum.semesters)
            return ToolResult.error(f"semester '{semester}' is not in the curriculum file (known: {known}): "
                                    "ask the student which semester they are in")
        return None

    @staticmethod
    def _mod(m) -> dict:
        return {"code": m.code, "name": m.name, "level": m.level, "optional": m.optional, "skills": m.skills,
                "topics": m.topics}

    def _split(self, semester: str):
        cur: Curriculum = self.state.curriculum
        idx = cur.index_of(semester)
        completed = [m for s in cur.semesters[:idx] for m in s.modules]
        current = list(cur.semesters[idx].modules)
        upcoming = [(s.id, m) for s in cur.semesters[idx + 1:] for m in s.modules]
        return completed, current, upcoming

    def get_curriculum_progress(self, semester: str | None) -> ToolResult:
        bad = self._check(semester)
        if bad:
            return bad
        completed, current, upcoming = self._split(semester)
        covered: dict[str, list[str]] = {}
        for m in completed + current:
            for sk in m.skills:
                covered.setdefault(sk, []).append(m.code)
        data = {"program": {"id": self.state.curriculum.program_id, "name": self.state.curriculum.program_name},
                "semester": semester, "completed": [self._mod(m) for m in completed],
                "current": [self._mod(m) for m in current],
                "upcoming": [{**self._mod(m), "semester": sid} for sid, m in upcoming],
                "skills_covered": covered}
        return ToolResult.ok(data)

    async def get_skill_gaps(self, semester: str | None, cluster: str, top_skills_fn: TopSkillsFn,
                             window: str = "6m", limit: int = 15) -> ToolResult:
        bad = self._check(semester)
        if bad:
            return bad
        completed, current, upcoming = self._split(semester)
        covered_map: dict[str, list[str]] = {}
        for m in completed + current:
            for sk in m.skills:
                covered_map.setdefault(sk.lower(), []).append(m.code)
        coming: dict[str, str] = {}
        in_curriculum = set(covered_map)
        for sid, m in upcoming:
            for sk in m.skills:
                coming.setdefault(sk.lower(), sid)
                in_curriculum.add(sk.lower())
        demand = await top_skills_fn(cluster, window, limit)
        base = {"semester": semester, "cluster": cluster, "window": window}
        if not demand.usable or not (demand.data or {}).get("items"):
            demand_status = demand.status if demand.status in ("empty", "unavailable") else "unavailable"
            progress = self.get_curriculum_progress(semester)
            return ToolResult.ok({**base, "demand_status": demand_status, "demand_reason": demand.reason,
                                  "as_of": demand.as_of, "covered": [], "gaps": [], "not_in_curriculum": [],
                                  "skills_covered": progress.data["skills_covered"]})
        covered, gaps, missing = [], [], []
        for it in demand.data["items"]:
            sk, count = it["skill"], it.get("count")
            key = sk.lower()
            if key in covered_map:
                covered.append({"skill": sk, "demand_count": count, "modules": covered_map[key]})
            else:
                gaps.append({"skill": sk, "demand_count": count, "coming_in": coming.get(key)})
                if key not in in_curriculum:
                    missing.append(sk)
        return ToolResult(status=demand.status, reason=demand.reason, as_of=demand.as_of,
                          data={**base, "demand_status": demand.status, "as_of": demand.as_of, "covered": covered,
                                "gaps": gaps, "not_in_curriculum": missing})
