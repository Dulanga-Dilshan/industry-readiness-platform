"""
ITPro scraper.

One task per category runs concurrently. Inside a category, pages are read
in order until a page has no new jobs (or max_pages is reached), so it does
not depend on how the pagination bar is built.

Usage (everything is a default argument of run()):
    data = await run()                                   # first page of every category
    data = await run(all_pages=True)                     # every page of every category
    data = await run(all_pages=True, max_pages=5)
    data = await run(categories=["devops-cloud", "ai-and-data"])
    data = await run(top_n=40, known_ids=existing_ids)
"""
import asyncio
import heapq
import re

import aiohttp
from bs4 import BeautifulSoup
from yarl import URL

from ..models import Job

BASE_URL = "https://itpro.lk/jobs/{category}/"
CATEGORIES = [
    "software-engineering",
    "quality-assurance",
    "web-development",
    "mobile-development",
    "devops-cloud",
    "ai-and-data",
    "hardware-networking",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Defaults (all can be overridden when calling run() or from the CLI)
ALL_PAGES = False        # False = first page of each category only
MAX_PAGES = 50           # safety cap per category when all_pages=True
TOP_N = None             # None / 0 = keep everything, newest first
CONCURRENCY_LIMIT = 10
REQUEST_TIMEOUT = 15


def safe_text(tag):
    return tag.get_text(strip=True) if tag else None


def parse_job_card(card):
    link_tag = card.select_one("a.jcl")
    time_tag = card.select_one("time.time-posted")

    return {
        "id": card.get("id"),
        "title": safe_text(card.select_one("h2.jc-title")),
        "link": link_tag["href"]
        if link_tag and link_tag.has_attr("href")
        else None,
        "company": safe_text(card.select_one("span.jc-company")),
        "location": safe_text(card.select_one("header span.la")),
        "datetime": time_tag["datetime"]
        if time_tag and time_tag.has_attr("datetime")
        else None,
        "posted_ago": safe_text(time_tag),
    }


def parse_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    return [parse_job_card(c) for c in soup.find_all("article", class_="job-card")]


def parse_description(html):
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("section", id="job-description")
    if section is None:
        return None
    text = "\n".join(
        line.strip() for line in section.get_text().splitlines() if line.strip()
    )
    return text[15:]


def page_url(category, page):
    base = BASE_URL.format(category=category)
    return base if page == 1 else f"{base}?p={page}&"


async def fetch_html(session, sem, url, quiet=False):
    """Return page text, or None on 404 / error."""
    async with sem:
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.text()
        except Exception as e:
            if not quiet:
                print(f"Error fetching {url}: {e}")
            return None


async def scrape_category(session, sem, category, all_pages, max_pages):
    """Collect the job cards of one category (first page or all pages)."""
    cards, seen = [], set()
    last_page = max_pages if all_pages else 1
    pages_read = 0

    for page in range(1, last_page + 1):
        html = await fetch_html(session, sem, page_url(category, page))
        if not html:
            break
        pages_read += 1
        page_cards = await asyncio.to_thread(parse_cards, html)
        new = [c for c in page_cards if c["id"] and c["id"] not in seen]
        if not new:  # empty page, or the site repeated the last page
            break
        seen.update(c["id"] for c in new)
        cards.extend(new)

    print(f"[{category}] {len(cards)} jobs from {pages_read} page(s)")
    return cards


JOB_ID = re.compile(r"/job/(\d+)")


async def get_description(session, sem, card, known):
    # Already stored by the backend: skip the request. The description stays
    # None, so the backend must not overwrite a stored description with None.
    if not card["link"] or str(card["id"]) in known:
        return None

    # Some slugs contain an encoded non-breaking space (%c2%a0). aiohttp
    # re-encodes it as %C2%A0 and the site rejects that, so first try the
    # link exactly as the site gave it (encoded=True), then the id-only URL.
    link = card["link"]
    candidates = [URL(link, encoded=True)]
    m = JOB_ID.search(link)
    if m:
        candidates.append(URL(f"https://itpro.lk/job/{m.group(1)}/", encoded=True))

    for i, url in enumerate(candidates):
        html = await fetch_html(session, sem, url, quiet=i < len(candidates) - 1)
        if html:
            return await asyncio.to_thread(parse_description, html)
    return None


async def run(
    categories=None,
    all_pages=ALL_PAGES,
    max_pages=MAX_PAGES,
    top_n=TOP_N,
    known_ids=None,
):
    """Scrape and return {id: job}, newest first. Reusable by a gRPC handler."""
    categories = categories or CATEGORIES
    known = {str(i) for i in (known_ids or ())}
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # One task per category, all running at the same time
        results = await asyncio.gather(
            *(scrape_category(session, sem, c, all_pages, max_pages) for c in categories)
        )

        # A job can appear in several categories: keep the first copy
        unique = {}
        for cards in results:
            for card in cards:
                unique.setdefault(card["id"], card)

        # Newest first; ISO dates compare chronologically, missing dates go last
        key = lambda c: c["datetime"] or ""
        if top_n:
            ordered = heapq.nlargest(top_n, unique.values(), key=key)
        else:
            ordered = sorted(unique.values(), key=key, reverse=True)

        print(f"{len(unique)} unique jobs, fetching descriptions for {len(ordered)}...")
        descriptions = await asyncio.gather(
            *(get_description(session, sem, c, known) for c in ordered)
        )

    data = {}
    for card, description in zip(ordered, descriptions):
        card["description"] = description
        data[str(card["id"])] = dict(sorted(card.items()))  # jobs newest-first, fields A-Z
    return data


NAME = "itpro"


async def fetch(**options):
    """Package entry point. Options are the keyword arguments of run()."""
    data = await run(**options)
    return [Job.from_dict(NAME, j) for j in data.values()]
