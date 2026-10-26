"""
LinkedIn (public guest listings) scraper.

Every keyword is searched concurrently. Inside a keyword, result pages are
read in order until a page is empty, max_pages_per_keyword is reached, or the
shared target_jobs count has been collected. Jobs older than max_age_days are
dropped. Then descriptions are fetched for the newest target_jobs jobs.

Usage (everything is a default argument of run()):
    data = await run()                                    # up to ~800 jobs, last 365 days
    data = await run(target_jobs=300, max_age_days=30)
    data = await run(keywords=["DevOps Engineer", "SOC Analyst"])
    data = await run(known_ids=existing_ids)              # skip stored descriptions
"""
import asyncio
import heapq
import re
from datetime import date, timedelta

import aiohttp
from bs4 import BeautifulSoup

from ..models import Job

SEARCH_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# One group per career cluster (+ generic entry-level terms).
KEYWORDS_BY_CLUSTER = {
    "software": [
        "Software Engineer",
        "Software Developer",
        "Web Developer",
        "Frontend Developer",
        "Backend Developer",
        "Full Stack Developer",
        "Mobile App Developer",
        "QA Engineer",
        "Software Quality Assurance",
        "Automation Test Engineer",
    ],
    "devops_cloud": [
        "DevOps Engineer",
        "Cloud Engineer",
        "Site Reliability Engineer",
        "Platform Engineer",
    ],
    "cybersecurity": [
        "Cyber Security",
        "Security Analyst",
        "SOC Analyst",
        "Information Security",
        "Penetration Tester",
    ],
    "networking_infra": [
        "Network Engineer",
        "Network Administrator",
        "System Administrator",
        "Infrastructure Engineer",
        "IT Support Engineer",
    ],
    "data_ai": [
        "Data Scientist",
        "Data Analyst",
        "Data Engineer",
        "Machine Learning Engineer",
        "AI Engineer",
        "Business Intelligence",
    ],
    "generic_entry": [
        "Intern",
        "Trainee",
        "Graduate Trainee",
        "Associate Engineer",
    ],
}
KEYWORDS = [kw for group in KEYWORDS_BY_CLUSTER.values() for kw in group]

# Defaults (all can be overridden through the arguments of run())
LOCATION = "Sri Lanka"
MAX_AGE_DAYS = 365            # drop anything posted earlier than this
TARGET_JOBS = 800             # stop searching once this many jobs are found (0 = no limit)
MAX_PAGES_PER_KEYWORD = 10    # 10 results per page
EXPERIENCE_LEVELS = "1,2"     # LinkedIn filter: 1 = Internship, 2 = Entry level. None = off
SORT_BY = "DD"                # "DD" = most recent first. None = LinkedIn's default (relevance)
PAGE_SIZE = 10

MAX_CONCURRENCY = 8
MAX_RETRIES = 4
REQUEST_TIMEOUT = 20

ID_PATTERN = re.compile(r"(\d{6,})")


def extract_job_id(card, link):
    urn_holder = card.find(attrs={"data-entity-urn": True})
    if urn_holder:
        return urn_holder["data-entity-urn"].split(":")[-1]
    match = ID_PATTERN.findall(link.split("?")[0])
    return match[-1] if match else None


def parse_search_page(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for card in soup.find_all("li"):
        link_el = card.find("a", class_="base-card__full-link") or card.find(
            "a", href=True
        )
        if not link_el:
            continue

        link = link_el["href"].split("?")[0]
        job_id = extract_job_id(card, link_el["href"])
        if not job_id:
            continue

        title_el = card.find("h3", class_="base-search-card__title")
        company_el = card.find("h4", class_="base-search-card__subtitle")
        location_el = card.find("span", class_="job-search-card__location")
        time_el = card.find("time")

        jobs.append(
            {
                "id": int(job_id) if job_id.isdigit() else job_id,
                "title": title_el.get_text(strip=True) if title_el else None,
                "company": company_el.get_text(strip=True) if company_el else None,
                "location": location_el.get_text(strip=True) if location_el else None,
                "link": link,
                "datetime": time_el.get("datetime") if time_el else None,
                "posted_ago": time_el.get_text(strip=True) if time_el else None,
            }
        )

    return jobs


def parse_description(html):
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("div", class_="show-more-less-html__markup")
    if not el:
        return None
    for br in el.find_all("br"):
        br.replace_with("\n")
    return "\n".join(
        line.strip() for line in el.get_text("\n").splitlines() if line.strip()
    )


async def fetch_text(session, sem, url, params=None):
    for attempt in range(MAX_RETRIES):
        try:
            async with sem:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    if resp.status not in (429, 500, 502, 503, 504, 999):
                        return None
                    retry_after = resp.headers.get("Retry-After")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            retry_after = None

        delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        await asyncio.sleep(delay)

    return None


async def search_keyword(
    session, sem, keyword, found, cutoff, max_age_days,
    max_pages, experience_levels, sort_by, target_jobs,
):
    """Read the result pages of one keyword into the shared `found` dict."""
    added = 0
    for page in range(max_pages):
        if target_jobs and len(found) >= target_jobs:
            break

        params = {
            "keywords": keyword,
            "location": LOCATION,
            "start": page * PAGE_SIZE,
        }
        # LinkedIn's date filter is only dependable up to a month; beyond
        # that the age limit is applied locally below.
        if max_age_days <= 30:
            params["f_TPR"] = f"r{max_age_days * 86400}"
        if experience_levels:
            params["f_E"] = experience_levels
        if sort_by:
            params["sortBy"] = sort_by

        html = await fetch_text(session, sem, SEARCH_API, params)
        jobs = parse_search_page(html) if html else []
        if not jobs:
            break

        for job in jobs:
            if job["datetime"] and job["datetime"] < cutoff:
                continue  # older than max_age_days
            if job["id"] not in found:
                found[job["id"]] = job
                added += 1

    print(f"[search] '{keyword}': {added} new jobs")


async def get_description(session, sem, job, known):
    # Already stored by the backend: skip the request. The description stays
    # None, so the backend must not overwrite a stored description with None.
    if str(job["id"]) in known:
        job["description"] = None
        return job

    html = await fetch_text(session, sem, DETAIL_API.format(job_id=job["id"]))
    job["description"] = parse_description(html) if html else None
    return job


async def run(
    keywords=None,
    max_age_days=MAX_AGE_DAYS,
    target_jobs=TARGET_JOBS,
    max_pages_per_keyword=MAX_PAGES_PER_KEYWORD,
    experience_levels=EXPERIENCE_LEVELS,
    sort_by=SORT_BY,
    known_ids=None,
):
    """Scrape and return {id: job}, newest first. Reusable by a gRPC handler."""
    keywords = keywords or KEYWORDS
    known = {str(i) for i in (known_ids or ())}
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENCY * 2, ttl_dns_cache=300)

    found = {}  # shared by all keyword tasks, de-duplicated by job id
    async with aiohttp.ClientSession(
        headers=HEADERS, timeout=timeout, connector=connector
    ) as session:
        await asyncio.gather(
            *(
                search_keyword(
                    session, sem, kw, found, cutoff, max_age_days,
                    max_pages_per_keyword, experience_levels, sort_by, target_jobs,
                )
                for kw in keywords
            )
        )

        # Newest first; ISO dates compare chronologically, missing dates go last.
        key = lambda j: j["datetime"] or ""
        if target_jobs:
            ordered = heapq.nlargest(target_jobs, found.values(), key=key)
        else:
            ordered = sorted(found.values(), key=key, reverse=True)

        new_count = sum(1 for j in ordered if str(j["id"]) not in known)
        print(f"\n{len(found)} unique jobs, {new_count} new, fetching descriptions...")

        detailed = await asyncio.gather(
            *(get_description(session, sem, job, known) for job in ordered)
        )

    # gather keeps order, so the result stays newest-first.
    return {str(job["id"]): dict(sorted(job.items())) for job in detailed}


NAME = "linkedin"


async def fetch(**options):
    """Package entry point. Options are the keyword arguments of run()."""
    data = await run(**options)
    return [Job.from_dict(NAME, j) for j in data.values()]
