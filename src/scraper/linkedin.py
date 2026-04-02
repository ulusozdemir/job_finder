import asyncio
import logging
import random
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from config import settings
from src.matcher.profile import SearchQuery

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com/jobs/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class RawJob:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    posted_time: str


def _build_search_url(query: SearchQuery, start: int = 0) -> str:
    params = {
        "keywords": query.keywords,
        "location": query.location,
        "f_TPR": query.time_posted,
        "start": str(start),
    }
    work_type_map = {"onsite": "1", "remote": "2", "hybrid": "3"}
    if query.work_type in work_type_map:
        params["f_WT"] = work_type_map[query.work_type]

    return f"{BASE_URL}?{urlencode(params)}"


def _parse_job_cards(html: str) -> list[RawJob]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[RawJob] = []

    cards = soup.select("div.base-card")

    for card in cards:
        try:
            title_el = card.select_one("h3.base-search-card__title")
            company_el = card.select_one("h4.base-search-card__subtitle a")
            location_el = card.select_one("span.job-search-card__location")
            link_el = card.select_one("a.base-card__full-link")
            time_el = card.select_one("time")

            if not title_el or not link_el:
                continue

            url = link_el.get("href", "").split("?")[0]
            job_id = card.get("data-entity-urn", "")
            if not job_id:
                job_id = url.rstrip("/").split("-")[-1] if url else ""

            jobs.append(
                RawJob(
                    job_id=job_id,
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "Unknown",
                    location=location_el.get_text(strip=True) if location_el else "",
                    url=url,
                    posted_time=time_el.get("datetime", "") if time_el else "",
                )
            )
        except Exception:
            logger.warning("Failed to parse a job card, skipping", exc_info=True)

    return jobs


async def _fetch_job_description(client: httpx.AsyncClient, url: str) -> str:
    """Fetch the full job description from the job detail page."""
    try:
        delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max)
        await asyncio.sleep(delay)

        resp = await client.get(url, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        desc_el = soup.select_one(
            "div.description__text, "
            "div.show-more-less-html__markup, "
            "section.description div.core-section-container__content"
        )
        if desc_el:
            return desc_el.get_text(separator="\n", strip=True)
    except Exception:
        logger.warning("Failed to fetch description for %s", url, exc_info=True)
    return ""


async def scrape_search(query: SearchQuery, max_pages: int = 2) -> list[RawJob]:
    """Scrape LinkedIn public job search results for a single query."""
    all_jobs: list[RawJob] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for page in range(max_pages):
            url = _build_search_url(query, start=page * 25)
            logger.info("Fetching %s", url)

            delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max)
            await asyncio.sleep(delay)

            try:
                resp = await client.get(url, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Search request failed: %s", e)
                break

            jobs = _parse_job_cards(resp.text)
            if not jobs:
                logger.info("No more results on page %d", page + 1)
                break

            all_jobs.extend(jobs)
            logger.info("Found %d jobs on page %d", len(jobs), page + 1)

    return all_jobs


async def fetch_descriptions(jobs: list[RawJob]) -> dict[str, str]:
    """Fetch descriptions for a batch of jobs. Returns {job_id: description}."""
    descriptions: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for job in jobs:
            desc = await _fetch_job_description(client, job.url)
            descriptions[job.job_id] = desc
    return descriptions
