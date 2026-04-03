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


async def _fetch_job_description(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Fetch the full job description and work type from the job detail page.

    Returns (description, work_type) where work_type is one of
    "Remote", "Hybrid", "On-site", or "" if not found.
    """
    try:
        delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max)
        await asyncio.sleep(delay)

        resp = await client.get(url, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        description = ""
        desc_el = soup.select_one(
            "div.description__text, "
            "div.show-more-less-html__markup, "
            "section.description div.core-section-container__content"
        )
        if desc_el:
            description = desc_el.get_text(separator="\n", strip=True)

        work_type = _parse_work_type(soup)

        return description, work_type
    except Exception:
        logger.warning("Failed to fetch description for %s", url, exc_info=True)
    return "", ""


def _parse_work_type(soup: BeautifulSoup) -> str:
    """Extract workplace type from the job detail page."""
    WORK_TYPE_LABELS = {"remote", "hybrid", "on-site", "on site"}

    for item in soup.select("li.description__job-criteria-item"):
        header = item.select_one("h3")
        value = item.select_one("span")
        if header and value:
            h_text = header.get_text(strip=True).lower()
            if "workplace" in h_text or "work type" in h_text:
                return value.get_text(strip=True)

    for span in soup.select("span.ui-label, span.workplace-type"):
        text = span.get_text(strip=True).lower()
        if text in WORK_TYPE_LABELS:
            return span.get_text(strip=True)

    return ""


async def scrape_page(query: SearchQuery, page: int) -> list[RawJob] | None:
    """Scrape a single page of LinkedIn search results.
    Returns list of jobs, or None if no more results / error.
    """
    url = _build_search_url(query, start=page * 25)
    logger.info("Fetching page %d: %s", page + 1, url)

    delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max)
    await asyncio.sleep(delay)

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=HEADERS, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Search request failed: %s", e)
            return None

        # LinkedIn 303-redirects back to page 0 when there are no more results
        if page > 0 and "start=0" in str(resp.url):
            logger.info("LinkedIn redirected to page 1 — no more results")
            return None

        jobs = _parse_job_cards(resp.text)
        if not jobs:
            logger.info("No results on page %d", page + 1)
            return None

        logger.info("Found %d jobs on page %d", len(jobs), page + 1)
        return jobs


async def fetch_descriptions(jobs: list[RawJob]) -> dict[str, tuple[str, str]]:
    """Fetch descriptions for a batch of jobs.

    Returns {job_id: (description, work_type)}.
    """
    results: dict[str, tuple[str, str]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for job in jobs:
            desc, wtype = await _fetch_job_description(client, job.url)
            results[job.job_id] = (desc, wtype)
    return results
