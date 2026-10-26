"""Starts the real gRPC server in-process on a free port and calls it with a client."""
import asyncio
from unittest.mock import patch

import grpc
import pytest

from grpc_server.proto import jobs_pb2 as pb
from grpc_server.proto import jobs_pb2_grpc as pb_grpc
from grpc_server.server import build_server
from scraper import service as scraper_service
from scraper.models import Job, parse_dt


async def source_a(**kw):
    return [
        Job("a", "1", title="DevOps Engineer", company="Acme", description="Docker, Kubernetes",
            posted_at=parse_dt("2026-10-20")),
        Job("a", "2", title="HR Intern", description="recruitment"),   # no date
    ]


async def with_server(call):
    server = build_server()
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            return await call(channel)
    finally:
        await server.stop(None)


def test_scrape_latest_streams_jobs_then_summary():
    async def call(channel):
        stub = pb_grpc.ScraperServiceStub(channel)
        request = pb.ScrapeLatestRequest(sources=["a"], known_keys=["a:2"])
        return [event async for event in stub.ScrapeLatest(request)]

    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        events = asyncio.run(with_server(call))

    *job_events, last = events
    assert last.WhichOneof("event") == "summary"
    assert last.summary.total == 2 and last.summary.new_jobs == 1 and dict(last.summary.counts) == {"a": 2}

    jobs = {e.job.key: e.job for e in job_events}
    new, known = jobs["a:1"], jobs["a:2"]
    assert new.is_new and new.cluster == "devops_cloud" and list(new.skills) == ["Docker", "Kubernetes"]
    assert new.HasField("posted_at") and new.posted_at.ToDatetime().year == 2026
    assert not known.is_new and known.cluster == "" and not known.HasField("posted_at")


def test_unknown_source_is_invalid_argument():
    async def call(channel):
        stub = pb_grpc.ScraperServiceStub(channel)
        return [e async for e in stub.ScrapeLatest(pb.ScrapeLatestRequest(sources=["nope"]))]

    with patch.dict(scraper_service.SCRAPERS, {"a": source_a}, clear=True):
        with pytest.raises(grpc.aio.AioRpcError) as err:
            asyncio.run(with_server(call))
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_classify_rpc():
    async def call(channel):
        stub = pb_grpc.ClassifierServiceStub(channel)
        request = pb.ClassifyRequest(items=[
            pb.ClassifyItem(key="k1", title="Network Engineer", description="CCNA"),
            pb.ClassifyItem(key="k2", title="Accountant"),
        ])
        return await stub.Classify(request)

    response = asyncio.run(with_server(call))
    assert [(r.key, r.cluster) for r in response.results] == [
        ("k1", "networking_infrastructure"), ("k2", "other")]
