from unittest.mock import patch

from fastapi.testclient import TestClient

from grpc_server.rest_api import app
from scraper import service as scraper_service
from scraper.models import Job, parse_dt


async def source_a(**kw):
    return [Job("a", "1", title="SOC Analyst", description="SIEM", posted_at=parse_dt("2026-10-20"))]


client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_scrape_latest_endpoint():
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        data = client.post("/scrape-latest", json={"sources": ["a"]}).json()
    assert data["summary"] == {"total": 1, "new_jobs": 1, "counts": {"a": 1}, "errors": {}}
    job = data["jobs"][0]
    assert job["key"] == "a:1" and job["cluster"] == "cybersecurity" and job["is_new"] is True


def test_unknown_source_is_400():
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        assert client.post("/scrape-latest", json={"sources": ["zzz"]}).status_code == 400


def test_classify_endpoint():
    r = client.post("/classify", json={"items": [{"key": "k", "title": "Data Analyst", "description": "Power BI, SQL"}]})
    assert r.json()[0]["cluster"] == "data_ai"
