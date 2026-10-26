"""Turn service objects into protobuf messages (gRPC) or plain dicts (REST)."""
from datetime import timezone

from google.protobuf.timestamp_pb2 import Timestamp

from .proto import jobs_pb2 as pb


def _timestamp(dt):
    ts = Timestamp()
    ts.FromDatetime(dt.astimezone(timezone.utc).replace(tzinfo=None))
    return ts


def job_to_proto(cj):
    job, c = cj.job, cj.classification
    msg = pb.ClassifiedJob(
        key=job.key,
        source=job.source,
        source_id=job.source_id,
        title=job.title or "",
        company=job.company or "",
        location=job.location or "",
        link=job.link or "",
        description=job.description or "",
        scraped_at=_timestamp(job.scraped_at),
        is_new=cj.is_new,
        cluster=c.cluster if c else "",
        confidence=c.confidence if c else 0.0,
        skills=list(c.skills) if c else [],
    )
    if job.posted_at:
        msg.posted_at.CopyFrom(_timestamp(job.posted_at))
    return msg


def summary_to_proto(result):
    return pb.ScrapeSummary(
        total=len(result.jobs),
        new_jobs=result.new_count,
        counts=result.counts,
        errors=result.errors,
    )


def job_to_json(cj):
    job, c = cj.job, cj.classification
    data = job.to_dict()
    data.update(
        key=job.key,
        posted_at=job.posted_at.isoformat() if job.posted_at else None,
        is_new=cj.is_new,
        cluster=c.cluster if c else None,
        confidence=c.confidence if c else None,
        skills=list(c.skills) if c else [],
    )
    return data
