"""Calls the running gRPC server the same way your backend will.

1. Start the server in one terminal:   uv run python -m grpc_server
2. Edit the settings below, then in a second terminal:
                                       uv run python scripts/try_client.py
"""
import asyncio

import grpc

from grpc_server.proto import jobs_pb2 as pb
from grpc_server.proto import jobs_pb2_grpc as pb_grpc

# ---- settings ---------------------------------------------------------------
SERVER = "localhost:50051"
SOURCES = ["itpro"]            # [] = all sources (slow: includes LinkedIn)
KNOWN_KEYS = []                # e.g. ["itpro:15030"] to see how known jobs behave
LIMIT = 0                      # 0 = no limit
# -----------------------------------------------------------------------------


async def main():
    request = pb.ScrapeLatestRequest(sources=SOURCES, known_keys=KNOWN_KEYS, limit=LIMIT)
    async with grpc.aio.insecure_channel(SERVER) as channel:
        stub = pb_grpc.ScraperServiceStub(channel)
        shown = 0
        async for event in stub.ScrapeLatest(request):
            if event.WhichOneof("event") == "job":
                job = event.job
                if shown < 15:  # print only the first 15 jobs
                    new = "new  " if job.is_new else "known"
                    print(f"{new} {job.key:22} {job.cluster or '-':26} {job.title[:50]}")
                shown += 1
            else:
                s = event.summary
                print(f"\n{shown} jobs received, {s.new_jobs} new")
                print("per source:", dict(s.counts))
                print("errors:    ", dict(s.errors) or "none")


asyncio.run(main())
