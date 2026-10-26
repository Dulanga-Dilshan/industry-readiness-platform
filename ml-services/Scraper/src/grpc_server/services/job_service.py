"""Application logic. The gRPC servicers and the REST test API both call these
functions, so nothing is written twice. scrape_latest() needs both the scraper
and the classifier, which is why it lives here and not inside either package."""
import asyncio
from dataclasses import dataclass, field

from classifier import Classification, classify_many
from scraper import Job, scrape_all

from .. import config


@dataclass
class ClassifiedJob:
    job: Job
    is_new: bool                                  # False = already stored by the backend
    classification: Classification | None = None  # only set for new jobs


@dataclass
class LatestResult:
    jobs: list[ClassifiedJob]                     # newest first
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def new_count(self) -> int:
        return sum(1 for j in self.jobs if j.is_new)


async def scrape_latest(
    known_keys=None,
    saved_keys_provider=None,
    sources=None,
    options=None,
    limit=None,
    max_age_days=None,
):
    """Scrape recent postings, classify the new ones, return everything.

    The backend runs this every 24 hours.

    known_keys           "source:id" keys already stored in the database. Over
                         gRPC the backend sends them in the request.
    saved_keys_provider  optional async callable returning stored keys; use it
                         when calling this in-process, e.g.
                         `saved_keys_provider=repo.list_job_keys`
    sources, options     passed to scraper.scrape_all (options override
                         config.LATEST_OPTIONS per source)
    max_age_days         shortcut for the LinkedIn age limit

    Jobs the backend already has come back with is_new=False and no
    classification (their description is not fetched again, so the backend only
    needs to update last_seen). New jobs are classified.
    """
    keys = set(known_keys or ())
    if saved_keys_provider is not None:
        keys.update(await saved_keys_provider())

    merged = {source: dict(opts) for source, opts in config.LATEST_OPTIONS.items()}
    for source, opts in (options or {}).items():
        merged.setdefault(source, {}).update(opts)
    if max_age_days:
        merged.setdefault("linkedin", {})["max_age_days"] = max_age_days

    result = await scrape_all(sources=sources, options=merged, limit=limit, known_keys=keys)

    new_jobs = [j for j in result.jobs if j.key not in keys]
    classifications = await asyncio.to_thread(
        classify_many, [(j.title, j.description) for j in new_jobs]
    )
    by_key = {j.key: c for j, c in zip(new_jobs, classifications)}

    jobs = [ClassifiedJob(j, j.key not in keys, by_key.get(j.key)) for j in result.jobs]
    return LatestResult(jobs, result.counts, result.errors)


async def classify_items(items):
    """items: list of (key, title, description) -> list of (key, Classification)."""
    results = await asyncio.to_thread(classify_many, [(t, d) for _, t, d in items])
    return [(key, c) for (key, _, _), c in zip(items, results)]
