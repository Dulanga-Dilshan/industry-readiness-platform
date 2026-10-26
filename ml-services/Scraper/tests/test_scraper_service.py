"""Run with:  uv run pytest   (or: uv run pytest)"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scraper import service
from scraper.models import Job, parse_dt, posted_ago


def job(source, sid, day):
    return Job(source, sid, title=f"{source}{sid}", posted_at=parse_dt(f"2026-10-{day:02d}"))


async def src_a(**kw):
    return [job("a", "1", 10), job("a", "2", 20)]


async def src_b(**kw):
    return [job("b", "1", 15), job("b", "9", None or 1)]


async def boom(**kw):
    raise RuntimeError("blocked")


def run(**kwargs):
    return asyncio.run(service.scrape_all(**kwargs))


def test_merge_and_newest_first():
    with patch.dict(service.SCRAPERS, {"a": src_a, "b": src_b}, clear=True):
        res = run(sources=["a", "b"])
    assert [j.key for j in res.jobs] == ["a:2", "b:1", "a:1", "b:9"]
    assert res.counts == {"a": 2, "b": 2} and res.errors == {}


def test_limit_keeps_newest():
    with patch.dict(service.SCRAPERS, {"a": src_a, "b": src_b}, clear=True):
        res = run(sources=["a", "b"], limit=2)
    assert [j.key for j in res.jobs] == ["a:2", "b:1"]


def test_failing_source_does_not_stop_others():
    with patch.dict(service.SCRAPERS, {"a": src_a, "x": boom}, clear=True):
        res = run(sources=["a", "x"])
    assert len(res.jobs) == 2
    assert "RuntimeError: blocked" in res.errors["x"] and "x" not in res.counts


def test_same_id_on_different_sources_does_not_collide():
    async def other(**kw):
        return [job("b", "1", 5)]
    async def same(**kw):
        return [job("a", "1", 5)]
    with patch.dict(service.SCRAPERS, {"a": same, "b": other}, clear=True):
        res = run(sources=["a", "b"])
    assert {j.key for j in res.jobs} == {"a:1", "b:1"}


def test_known_keys_are_routed_per_source():
    seen = {}
    async def spy(**kw):
        seen.update(kw)
        return []
    with patch.dict(service.SCRAPERS, {"a": spy}, clear=True):
        run(sources=["a"], known_keys={"a:1", "a:2", "b:7"}, options={"a": {"all_pages": True}})
    assert seen == {"all_pages": True, "known_ids": {"1", "2"}}


def test_unknown_source_raises():
    with patch.dict(service.SCRAPERS, {"a": src_a}, clear=True):
        with pytest.raises(ValueError):
            run(sources=["nope"])


def test_parse_dt_variants():
    assert parse_dt("2026-10-08") == datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert parse_dt("2026-06-23 16:48:41").hour == 16
    assert parse_dt("2026-10-08T10:00:00Z").tzinfo is not None
    assert parse_dt("garbage") is None and parse_dt(None) is None


def test_to_dict_keeps_the_json_format():
    j = Job("linkedin", "123", title="T", company="C", posted_at=parse_dt("2026-10-08"))
    d = j.to_dict()
    assert list(d) == sorted(d)
    assert d["id"] == "123" and d["datetime"] == "2026-10-08" and d["source"] == "linkedin"
    assert posted_ago(parse_dt("2026-10-01"), now=parse_dt("2026-10-15")) == "2 weeks ago"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
