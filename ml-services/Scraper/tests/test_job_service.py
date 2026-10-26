import asyncio
from unittest.mock import patch

import pytest

from grpc_server.services import classify_items, scrape_latest
from scraper import service as scraper_service
from scraper.models import Job, parse_dt


def job(sid, day, title="DevOps Engineer", desc="Docker and Kubernetes"):
    return Job("a", sid, title=title, description=desc, posted_at=parse_dt(f"2026-10-{day:02d}"))


async def source_a(**kw):
    return [job("1", 10), job("2", 20, title="HR Intern", desc="recruitment")]


def run(coro):
    return asyncio.run(coro)


def test_new_jobs_are_classified_known_jobs_are_not():
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        res = run(scrape_latest(sources=["a"], known_keys=["a:1"]))
    by_key = {cj.job.key: cj for cj in res.jobs}
    assert by_key["a:1"].is_new is False and by_key["a:1"].classification is None
    assert by_key["a:2"].is_new is True and by_key["a:2"].classification.cluster == "other"
    assert res.new_count == 1 and res.counts == {"a": 2}


def test_saved_keys_provider_is_used():
    async def provider():
        return ["a:1", "a:2"]
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        res = run(scrape_latest(sources=["a"], saved_keys_provider=provider))
    assert res.new_count == 0


def test_new_job_gets_cluster_and_skills():
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        res = run(scrape_latest(sources=["a"]))
    first = next(cj for cj in res.jobs if cj.job.key == "a:1")
    assert first.classification.cluster == "devops_cloud"
    assert set(first.classification.skills) == {"Docker", "Kubernetes"}


def test_options_reach_the_scrapers_and_defaults_are_applied():
    seen = {}
    async def spy(**kw):
        seen.update(kw)
        return []
    with patch.dict(scraper_service.SCRAPERS, {"linkedin": spy}, clear=True):
        run(scrape_latest(sources=["linkedin"], max_age_days=7, options={"linkedin": {"target_jobs": 50}}))
    assert seen == {"max_age_days": 7, "target_jobs": 50}


def test_failed_source_is_reported_not_raised():
    async def boom(**kw):
        raise RuntimeError("blocked")
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a, "x": boom}, clear=True):
        res = run(scrape_latest(sources=["a", "x"]))
    assert len(res.jobs) == 2 and "blocked" in res.errors["x"]


def test_unknown_source_raises():
    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        with pytest.raises(ValueError):
            run(scrape_latest(sources=["nope"]))


def test_classify_items():
    out = run(classify_items([("k1", "SOC Analyst", "SIEM"), ("k2", "HR Intern", "")]))
    assert [(k, c.cluster) for k, c in out] == [("k1", "cybersecurity"), ("k2", "other")]
