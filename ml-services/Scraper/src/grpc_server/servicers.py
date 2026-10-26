"""gRPC handlers. Thin on purpose: read the request, call a service, convert."""
import grpc

from . import converters
from .proto import jobs_pb2 as pb
from .proto import jobs_pb2_grpc as pb_grpc
from .services import classify_items, scrape_latest


class ScraperServicer(pb_grpc.ScraperServiceServicer):
    async def ScrapeLatest(self, request, context):
        try:
            result = await scrape_latest(
                known_keys=list(request.known_keys),
                sources=list(request.sources) or None,
                limit=request.limit or None,
                max_age_days=request.max_age_days or None,
            )
        except ValueError as exc:  # unknown source name
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        for cj in result.jobs:
            yield pb.ScrapeEvent(job=converters.job_to_proto(cj))
        yield pb.ScrapeEvent(summary=converters.summary_to_proto(result))


class ClassifierServicer(pb_grpc.ClassifierServiceServicer):
    async def Classify(self, request, context):
        items = [(i.key, i.title, i.description) for i in request.items]
        results = await classify_items(items)
        return pb.ClassifyResponse(
            results=[
                pb.ClassifyResult(
                    key=key,
                    cluster=c.cluster,
                    confidence=c.confidence,
                    skills=list(c.skills),
                )
                for key, c in results
            ]
        )
