"""Rootcode careers page: JSON API (one company, small and slow-moving)."""
import asyncio
import re

import requests
from bs4 import BeautifulSoup

from ..models import Job, parse_dt

NAME = "rootcode"
API_ENDPOINT = "https://rootcode.ai/api/jobs"
CAREERS_URL = "https://rootcode.ai/careers/"
REQUEST_TIMEOUT = 30

# The API's real posted-date field, if it has one (check list(job.keys())).
# While None, posted_at stays empty and these jobs sort last.
DATE_FIELD = None


def generate_job_url(text, job_id):
    text = text.lower().replace("/", "-")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return f"{CAREERS_URL}{text.strip('-')}-{job_id}"


def clean_description(html):
    text = BeautifulSoup(html or "", "html.parser").get_text(separator="\n").strip()
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _fetch_raw():
    r = requests.get(API_ENDPOINT, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["results"]


async def fetch(known_ids=None):
    """known_ids is accepted for a uniform interface but unused."""
    raw = await asyncio.to_thread(_fetch_raw)
    return [
        Job(
            source=NAME,
            source_id=str(j["id"]),
            title=j["position_name"],
            company="Rootcode",
            location=j.get("location_display"),
            link=generate_job_url(j["position_name"], j["id"]),
            description=clean_description(j.get("description")),
            posted_at=parse_dt(j.get(DATE_FIELD)) if DATE_FIELD else None,
        )
        for j in raw
    ]
