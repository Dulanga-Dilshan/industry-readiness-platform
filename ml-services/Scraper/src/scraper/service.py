"""Runs the scrapers together and merges their results."""
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from .config import DEFAULT_SOURCES
from .models import Job
from .scrapers import SCRAPERS
from .sorting import newest

log = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    jobs: list[Job]                                   # newest first
    counts: dict[str, int] = field(default_factory=dict)   # jobs returned per source
    errors: dict[str, str] = field(default_factory=dict)   # source -> error message


async def scrape_all(sources=None, options=None, limit=None, known_keys=None):
    """Run the chosen scrapers at the same time and merge the jobs.

    sources     names to run (default: config.DEFAULT_SOURCES)
    options     per-source keyword arguments,
                e.g. {"itpro": {"all_pages": True}, "linkedin": {"target_jobs": 300}}
    limit       keep only the N newest jobs overall (default: keep all)
    known_keys  job keys ("source:id") already in the database; scrapers skip
                the description request for them (their description is None,
                so do not overwrite a stored description with None)

    A source that fails is reported in result.errors; the others still return.
    """
    names = list(sources or DEFAULT_SOURCES)
    options = options or {}

    unknown = [n for n in names if n not in SCRAPERS]
    if unknown:
        raise ValueError(f"Unknown source(s): {unknown}. Available: {sorted(SCRAPERS)}")

    known = defaultdict(set)
    for key in known_keys or ():
        source, _, source_id = key.partition(":")
        known[source].add(source_id)

    calls = []
    for name in names:
        kwargs = dict(options.get(name, {}))
        if name in known and "known_ids" not in kwargs:
            kwargs["known_ids"] = known[name]
        calls.append(SCRAPERS[name](**kwargs))

    results = await asyncio.gather(*calls, return_exceptions=True)

    merged, counts, errors = {}, {}, {}
    for name, res in zip(names, results):
        if isinstance(res, BaseException):
            errors[name] = f"{type(res).__name__}: {res}"
            log.error("Source %s failed: %s", name, errors[name])
            continue
        counts[name] = len(res)
        for job in res:
            merged.setdefault(job.key, job)

    return ScrapeResult(jobs=newest(merged.values(), limit), counts=counts, errors=errors)
