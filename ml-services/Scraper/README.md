# Scraper (ML services part)

One project with three packages. The backend (FastAPI) is the only client; it calls
this server over gRPC once every 24 hours and stores the results itself.

```
src/
  scraper/       scrapers for ITPro, LinkedIn, Rooster, Rootcode -> list[Job]
  classifier/    job title + description -> career cluster + skills
  grpc_server/   gRPC server, services layer, REST test API
    proto/       jobs.proto (the contract) + generated jobs_pb2*.py
    services/    scrape_latest(), classify_items()   <- the logic
    servicers.py gRPC handlers (thin, call services/)
    rest_api.py  REST test API (same services, for testing only)
scripts/generate_proto.py
tests/
```

Dependency direction: grpc_server -> scraper, classifier. The scraper and classifier
do not know about each other or about gRPC.

## Setup

```
uv add aiohttp beautifulsoup4 requests grpcio protobuf fastapi uvicorn
uv add --dev pytest grpcio-tools httpx
uv run python scripts/generate_proto.py     # only after editing jobs.proto
```

## Run

```
uv run python -m grpc_server
```
- gRPC on port 50051. No other server software is needed: this process is the server.
- REST test API on http://127.0.0.1:8000/docs (set ENABLE_REST=0 to turn it off).
- Ports and limits are environment variables, see grpc_server/config.py.

## Call it from the backend

Copy `jobs.proto` into the backend project and generate stubs there, then:

```python
import grpc
from grpc_server.proto import jobs_pb2 as pb, jobs_pb2_grpc as pb_grpc   # your generated module paths

async with grpc.aio.insecure_channel("ml-services:50051") as channel:
    stub = pb_grpc.ScraperServiceStub(channel)
    request = pb.ScrapeLatestRequest(known_keys=await repo.list_job_keys())
    async for event in stub.ScrapeLatest(request):
        if event.WhichOneof("event") == "job":
            job = event.job        # job.is_new, job.cluster, job.skills ...
        else:
            print(event.summary)   # last event: totals and per-source errors
```

For every job with `is_new=False` only update `last_seen_at`; its description is empty
because it was not fetched again.

## Tests

```
uv run pytest
```

## Check it yourself (Windows, VS Code)

```
copy pyproject.example.toml pyproject.toml     (skip if you merged it into your own)
uv sync
uv run pytest                                  # 1. all tests, fake data, no internet needed
uv run python -m grpc_server                   # 2. start the server (leave it running)
```
Then, in a second terminal while the server runs:
- Open http://127.0.0.1:8000/docs. Try `POST /classify` first (no internet needed), then
  `POST /scrape-latest` with `{"sources": ["itpro"]}` (scrapes the real site).
- `uv run python scripts/try_client.py` calls the gRPC server the way the backend will.

Do not press VS Code's "Run" button on files inside `src/`; start the server with
`uv run python -m grpc_server`. Stop it with Ctrl+C. If a port is busy, set
`GRPC_PORT` / `REST_PORT` first (PowerShell: `$env:GRPC_PORT="50052"`).

If a source shows 0 jobs and no error in the summary, look at the server terminal:
the scrapers print the failed request there.
