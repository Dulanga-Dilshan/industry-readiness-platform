"""Rooster (rooster.jobs): JSON API. Internship listings in Sri Lanka, ICT only."""
import asyncio

import requests

from ..models import Job, parse_dt

NAME = "rooster"
API_URL = "https://api.rooster.jobs/jobSearch/jobs/search"
PAGE_LIMIT = 100
ICT_CLASS = "Information & Communication Technology"
REQUEST_TIMEOUT = 30

# TODO: the job page URL pattern is not known yet. Open a job on rooster.jobs,
# copy its address and replace the id part with {id}. While this is None the
# link field stays empty (a guessed link would send users to a dead page).
LINK_TEMPLATE = None


def _fetch_raw(job_types, country):
    """Blocking: read every result page. Runs in a worker thread."""
    jobs, page = [], 1
    while True:
        payload = {
            "query": [],
            "limit": PAGE_LIMIT,
            "page": page,
            "filters": {"jobTypes": list(job_types), "country": country},
        }
        r = requests.post(API_URL, json=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        body = r.json()["body"]
        if not body["data"]:
            break
        jobs.extend(body["data"])
        if len(jobs) >= body["count"]:
            break
        page += 1
    return jobs


def _to_job(raw):
    return Job(
        source=NAME,
        source_id=str(raw["id"]),
        title=raw.get("title"),
        company=raw.get("company_name"),
        location=raw.get("location"),
        link=LINK_TEMPLATE.format(id=raw["id"]) if LINK_TEMPLATE else None,
        description=raw.get("description"),
        posted_at=parse_dt(raw.get("created_at")),  # no timezone in the API: assumed UTC
    )


async def fetch(job_types=("internship",), country="Sri Lanka", ict_only=True, known_ids=None):
    """known_ids is accepted for a uniform interface but unused: the listing
    already contains the description, so there is no extra request to skip."""
    raw = await asyncio.to_thread(_fetch_raw, job_types, country)
    return [
        _to_job(j)
        for j in raw
        if "Sri Lanka" in (j.get("location") or "")
        and (not ict_only or j.get("class") == ICT_CLASS)
    ]
