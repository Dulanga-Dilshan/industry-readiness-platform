"""The five read-only backend tools (contract: contracts/backend_tools.openapi.yaml)."""
from __future__ import annotations

from advisor.config.settings import CLUSTERS, WINDOWS

from .backend_client import BackendClient
from .base import Tool, ToolContext
from .envelope import ToolResult


def _items(required: tuple[str, ...], key: str = "items"):
    def check(body: dict) -> str | None:
        items = body.get(key)
        if not isinstance(items, list):
            return f"'{key}' must be a list"
        for it in items:
            if not isinstance(it, dict) or any(r not in it for r in required):
                return f"an item in '{key}' is missing one of {required}"
        return None
    return check


def _job(body: dict) -> str | None:
    job = body.get("job")
    if not isinstance(job, dict) or not all(k in job for k in ("key", "title")):
        return "'job' must be an object with key and title"
    return None


class BackendTools:
    def __init__(self, client: BackendClient):
        self.client = client

    async def get_cluster_demand(self, window: str, user_id: str | None = None) -> ToolResult:
        return await self.client.get("get_cluster_demand", "/api/v1/analytics/cluster-demand", {"window": window},
                                     user_id, _items(("cluster", "count", "share_pct")))

    async def get_top_skills(self, cluster: str, window: str = "6m", limit: int = 15, user_id: str | None = None) -> ToolResult:
        return await self.client.get("get_top_skills", "/api/v1/analytics/top-skills",
                                     {"cluster": cluster, "window": window, "limit": limit}, user_id,
                                     _items(("skill", "count")))

    async def get_demand_trend(self, window: str = "1y", cluster: str | None = None, skill: str | None = None,
                               granularity: str = "month", user_id: str | None = None) -> ToolResult:
        if bool(cluster) == bool(skill):
            return ToolResult.error("give exactly one of cluster or skill")
        return await self.client.get("get_demand_trend", "/api/v1/analytics/demand-trend",
                                     {"cluster": cluster, "skill": skill, "window": window, "granularity": granularity},
                                     user_id, _items(("period", "count"), "series"))

    async def search_jobs(self, query: str | None = None, cluster: str | None = None, skills: list[str] | None = None,
                          location: str | None = None, posted_within_days: int | None = None, limit: int = 10,
                          user_id: str | None = None) -> ToolResult:
        params = {"query": query, "cluster": cluster, "skills": ",".join(skills or []) or None, "location": location,
                  "posted_within_days": posted_within_days, "limit": limit}
        return await self.client.get("search_jobs", "/api/v1/jobs", params, user_id,
                                     _items(("key", "title", "company", "link")), has_as_of=False)

    async def get_job(self, key: str, user_id: str | None = None) -> ToolResult:
        res = await self.client.get("get_job", f"/api/v1/jobs/{key}", {}, user_id, _job, has_as_of=False)
        return res


def register_backend_tools(registry, bt: BackendTools) -> None:
    window = {"type": "string", "enum": WINDOWS}
    cluster = {"type": "string", "enum": CLUSTERS}

    async def cd(a: dict, ctx: ToolContext):
        return await bt.get_cluster_demand(a.get("window", "6m"), ctx.user_id)

    async def ts(a: dict, ctx: ToolContext):
        return await bt.get_top_skills(a["cluster"], a.get("window", "6m"), a.get("limit", 15), ctx.user_id)

    async def dt(a: dict, ctx: ToolContext):
        return await bt.get_demand_trend(a.get("window", "1y"), a.get("cluster"), a.get("skill"), "month", ctx.user_id)

    async def sj(a: dict, ctx: ToolContext):
        return await bt.search_jobs(a.get("query"), a.get("cluster"), a.get("skills"), a.get("location"),
                                    a.get("posted_within_days"), a.get("limit", 10), ctx.user_id)

    async def gj(a: dict, ctx: ToolContext):
        return await bt.get_job(a["key"], ctx.user_id)

    registry.register(Tool("get_cluster_demand", "Number of job postings per career cluster in a time window.",
                           {"type": "object", "properties": {"window": window}, "required": []}, cd))
    registry.register(Tool("get_top_skills", "Most demanded skills in job postings for one career cluster.",
                           {"type": "object", "properties": {"cluster": cluster, "window": window,
                                                             "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
                            "required": ["cluster"]}, ts))
    registry.register(Tool("get_demand_trend", "Monthly posting counts for one cluster or one skill (give exactly one).",
                           {"type": "object", "properties": {"cluster": cluster, "skill": {"type": "string", "maxLength": 60},
                                                             "window": window}, "required": []}, dt))
    registry.register(Tool("search_jobs", "Search current job postings.",
                           {"type": "object", "properties": {
                               "query": {"type": "string", "maxLength": 120}, "cluster": cluster,
                               "skills": {"type": "array", "maxItems": 8}, "location": {"type": "string", "maxLength": 60},
                               "posted_within_days": {"type": "integer", "minimum": 1, "maximum": 365},
                               "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": []}, sj))
    registry.register(Tool("get_job", "Get one job posting with its description, by key.",
                           {"type": "object", "properties": {"key": {"type": "string", "maxLength": 80}},
                            "required": ["key"]}, gj))
