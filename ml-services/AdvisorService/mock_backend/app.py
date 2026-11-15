"""Mock backend for development and tests. ALL NUMBERS HERE ARE FAKE PLACEHOLDERS, not research data.

Run:  uvicorn mock_backend.app:app --port 9000
Switch behaviour:  POST /mock/mode {"mode": "500"}   (ok, slow, timeout, 500, 404, 429, malformed, empty, stale)
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

MODES = {"ok", "slow", "timeout", "500", "404", "429", "malformed", "empty", "stale"}
CLUSTERS = ["Software Engineering", "DevOps/Cloud", "Cybersecurity", "Networking & Infrastructure", "Data & AI"]
FAKE_COUNTS = [400, 220, 90, 110, 180]   # MOCK values


def _jobs():
    return [{"key": f"mock-{i}", "title": f"MOCK job {i}", "company": "MOCK Company", "cluster": CLUSTERS[i % 5],
             "skills": ["Python", "SQL"], "location": "Colombo", "posted_at": "2026-11-10T00:00:00Z",
             "link": f"https://example.com/jobs/mock-{i}"} for i in range(1, 6)]


def create_app(secret: str | None = None, slow_seconds: float = 3.0, timeout_seconds: float = 30.0) -> FastAPI:
    app = FastAPI(title="mock backend")
    secret = secret or os.environ.get("MOCK_SECRET", "test-secret")
    state = {"mode": "ok"}

    def as_of() -> str:
        age = timedelta(days=10) if state["mode"] == "stale" else timedelta(0)
        return (datetime.now(timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def gate(request: Request, secret_h: str | None):
        if secret_h != secret:
            raise HTTPException(401, "bad secret")
        mode = state["mode"]
        if mode == "slow":
            await asyncio.sleep(slow_seconds)
        elif mode == "timeout":
            await asyncio.sleep(timeout_seconds)
        elif mode == "500":
            raise HTTPException(500, "boom")
        elif mode == "404":
            raise HTTPException(404, "not built yet")
        elif mode == "429":
            return JSONResponse({"detail": "slow down"}, 429, headers={"Retry-After": "1"})
        return None

    def wrap(items_key: str, items: list):
        mode = state["mode"]
        if mode == "malformed":
            return JSONResponse({"as_of": as_of(), items_key: "not-a-list"})
        if mode == "empty":
            items = []
        return {"mock": True, "as_of": as_of(), items_key: items}

    @app.get("/mock/mode")
    async def get_mode():
        return state

    @app.post("/mock/mode")
    async def set_mode(body: dict):
        if body.get("mode") not in MODES:
            raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
        state["mode"] = body["mode"]
        return state

    @app.get("/api/v1/analytics/cluster-demand")
    async def cluster_demand(request: Request, window: str = "6m", x_service_secret: str | None = Header(None)):
        if (r := await gate(request, x_service_secret)):
            return r
        total = sum(FAKE_COUNTS)
        return wrap("items", [{"cluster": c, "count": n, "share_pct": round(100 * n / total, 1)}
                              for c, n in zip(CLUSTERS, FAKE_COUNTS)])

    @app.get("/api/v1/analytics/top-skills")
    async def top_skills(request: Request, cluster: str, window: str = "6m", limit: int = 15,
                         x_service_secret: str | None = Header(None)):
        if (r := await gate(request, x_service_secret)):
            return r
        skills = [("Python", 120), ("SQL", 100), ("Docker", 80), ("Git", 75), ("Kubernetes", 40)]
        return wrap("items", [{"skill": s, "count": c} for s, c in skills][:limit])

    @app.get("/api/v1/analytics/demand-trend")
    async def demand_trend(request: Request, window: str = "1y", cluster: str | None = None, skill: str | None = None,
                           granularity: str = "month", x_service_secret: str | None = Header(None)):
        if (r := await gate(request, x_service_secret)):
            return r
        return wrap("series", [{"period": f"2026-{m:02d}", "count": 50 + 5 * m} for m in range(6, 12)])

    @app.get("/api/v1/jobs")
    async def jobs(request: Request, limit: int = 10, x_service_secret: str | None = Header(None)):
        if (r := await gate(request, x_service_secret)):
            return r
        return wrap("items", _jobs()[:limit]) | {"total": 5} if state["mode"] == "ok" else wrap("items", _jobs()[:limit])

    @app.get("/api/v1/jobs/{key}")
    async def job(request: Request, key: str, x_service_secret: str | None = Header(None)):
        if (r := await gate(request, x_service_secret)):
            return r
        if state["mode"] == "malformed":
            return {"job": "bad"}
        return {"mock": True, "job": {**_jobs()[0], "key": key, "description": "MOCK description text."}}

    return app


app = create_app()
