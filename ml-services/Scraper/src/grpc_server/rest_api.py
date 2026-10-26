"""REST API for testing only. It calls the same services as the gRPC handlers,
so what works here works over gRPC. Open http://127.0.0.1:8000/docs to try it."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import converters
from .services import classify_items, scrape_latest

app = FastAPI(title="Scraper and classifier: test API")


class ScrapeBody(BaseModel):
    sources: list[str] | None = Field(None, description='e.g. ["itpro"]; empty = all')
    known_keys: list[str] = Field(default_factory=list, description='e.g. ["itpro:15030"]')
    limit: int | None = None
    max_age_days: int | None = None


class ClassifyItemBody(BaseModel):
    key: str = ""
    title: str
    description: str = ""


class ClassifyBody(BaseModel):
    items: list[ClassifyItemBody]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/scrape-latest")
async def scrape_latest_endpoint(body: ScrapeBody):
    """Runs the real scrapers. While testing, use sources=["itpro"] to keep it small."""
    try:
        result = await scrape_latest(
            known_keys=body.known_keys,
            sources=body.sources or None,
            limit=body.limit,
            max_age_days=body.max_age_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "summary": {
            "total": len(result.jobs),
            "new_jobs": result.new_count,
            "counts": result.counts,
            "errors": result.errors,
        },
        "jobs": [converters.job_to_json(cj) for cj in result.jobs],
    }


@app.post("/classify")
async def classify_endpoint(body: ClassifyBody):
    items = [(i.key, i.title, i.description) for i in body.items]
    results = await classify_items(items)
    return [
        {"key": key, "cluster": c.cluster, "confidence": c.confidence,
         "skills": list(c.skills), "scores": c.scores}
        for key, c in results
    ]
