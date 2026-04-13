import asyncio
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from config import settings
from src.matcher.profile import SearchQuery

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com/jobs/search"
SEE_MORE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

RESULTS_PER_PAGE = 25
MAX_PAGES_SAFETY = 40  # 40 * 25 = 1000 max (LinkedIn's hard ceiling)


@dataclass
class RawJob:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    posted_time: str


def _build_params(query: SearchQuery, start: int = 0) -> dict:
    params = {
        "keywords": query.keywords,
        "location": query.location,
        "f_TPR": query.time_posted,
        "start": str(start),
    }
    work_type_map = {"onsite": "1", "remote": "2", "hybrid": "3"}
    if query.work_type in work_type_map:
        params["f_WT"] = work_type_map[query.work_type]
    return params


def _build_search_url(query: SearchQuery, start: int = 0) -> str:
    return f"{BASE_URL}?{urlencode(_build_params(query, start))}"


def _build_see_more_url(query: SearchQuery, start: int = 0) -> str:
    return f"{SEE_MORE_URL}?{urlencode(_build_params(query, start))}"


def _parse_total_results(html: str) -> int | None:
    """Extract total result count from the initial search page."""
    soup = BeautifulSoup(html, "html.parser")
    count_el = soup.select_one(
        "span.results-context-header__job-count, "
        "span.results-context-header__new-jobs"
    )
    if count_el:
        text = count_el.get_text(strip=True).replace(",", "").replace(".", "").replace("+", "")
        match = re.search(r"\d+", text)
        if match:
            return int(match.group())
    text_full = soup.get_text()
    match = re.search(r"([\d,.]+)\+?\s+results?", text_full, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", "").replace(".", ""))
    return None


def _parse_job_cards(html: str) -> list[RawJob]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[RawJob] = []

    cards = soup.select("div.base-card, li.base-card")

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


async def scrape_all_pages(query: SearchQuery) -> list[RawJob]:
    """Scrape ALL available pages for a search query using LinkedIn's
    seeMoreJobPostings API (same as infinite scroll on the website).

    Returns a flat list of all scraped jobs.
    """
    all_jobs: list[RawJob] = []
    seen_ids: set[str] = set()
    total_results: int | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        next_start = 0
        for page in range(MAX_PAGES_SAFETY):
            if total_results is not None and next_start >= total_results:
                logger.info("Reached total results (%d), stopping", total_results)
                break

            if page == 0:
                url = _build_search_url(query, start=0)
            else:
                url = _build_see_more_url(query, start=next_start)

            logger.info("Fetching page %d (start=%d): %s", page + 1, next_start, url)

            delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max)
            await asyncio.sleep(delay)

            try:
                resp = await client.get(url, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Search request failed on page %d: %s", page + 1, e)
                break

            if page > 0 and "start=0" in str(resp.url):
                logger.info("LinkedIn redirected to start=0 — no more results")
                break

            if page == 0:
                total_results = _parse_total_results(resp.text)
                if total_results is not None:
                    logger.info("LinkedIn reports ~%d total results", total_results)

            jobs = _parse_job_cards(resp.text)
            if not jobs:
                logger.info("No results on page %d, stopping pagination", page + 1)
                break

            new_count = 0
            for job in jobs:
                if job.job_id not in seen_ids:
                    seen_ids.add(job.job_id)
                    all_jobs.append(job)
                    new_count += 1

            next_start = len(all_jobs)

            logger.info(
                "Page %d: %d cards, %d new (total so far: %d, next start: %d)",
                page + 1, len(jobs), new_count, len(all_jobs), next_start,
            )

            if new_count == 0:
                logger.info("No new jobs on page %d, stopping", page + 1)
                break

    logger.info("Scraped %d total unique jobs for query '%s'", len(all_jobs), query.keywords)
    return all_jobs


async def scrape_page(query: SearchQuery, page: int) -> list[RawJob] | None:
    """Scrape a single page of LinkedIn search results.
    Returns list of jobs, or None if no more results / error.

    DEPRECATED: Use scrape_all_pages() instead for full results.
    Kept for backward compatibility.
    """
    if page == 0:
        url = _build_search_url(query, start=0)
    else:
        url = _build_see_more_url(query, start=page * RESULTS_PER_PAGE)
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

        if page > 0 and "start=0" in str(resp.url):
            logger.info("LinkedIn redirected to page 1 — no more results")
            return None

        jobs = _parse_job_cards(resp.text)
        if not jobs:
            logger.info("No results on page %d", page + 1)
            return None

        logger.info("Found %d jobs on page %d", len(jobs), page + 1)
        return jobs


async def _fetch_job_description(client: httpx.AsyncClient, url: str, title: str = "") -> tuple[str, str]:
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

        work_type = _parse_work_type(soup, title=title)

        return description, work_type
    except Exception:
        logger.warning("Failed to fetch description for %s", url, exc_info=True)
    return "", ""


_WORK_TYPE_MAP = {
    "remote": "Remote", "uzaktan": "Remote",
    "hybrid": "Hybrid", "hibrit": "Hybrid",
    "on-site": "On-site", "on site": "On-site",
    "onsite": "On-site", "yerinde": "On-site", "ofiste": "On-site",
}


def _parse_work_type(soup: BeautifulSoup, title: str = "") -> str:
    """Extract workplace type from the job detail page, title, or description."""
    for item in soup.select("li.description__job-criteria-item"):
        header = item.select_one("h3")
        value = item.select_one("span")
        if header and value:
            h_text = header.get_text(strip=True).lower()
            if any(k in h_text for k in ("workplace", "work type", "iş yeri")):
                return value.get_text(strip=True)

    for span in soup.select("span.ui-label, span.workplace-type"):
        text = span.get_text(strip=True).lower()
        if text in _WORK_TYPE_MAP:
            return _WORK_TYPE_MAP[text]

    title_lower = title.lower()
    for keyword, label in _WORK_TYPE_MAP.items():
        if keyword in title_lower:
            return label

    for span in soup.select("span.topcard__flavor"):
        text = span.get_text(strip=True).lower()
        for keyword, label in _WORK_TYPE_MAP.items():
            if keyword in text:
                return label

    desc_el = soup.select_one(
        "div.description__text, "
        "div.show-more-less-html__markup, "
        "section.description div.core-section-container__content"
    )
    if desc_el:
        desc_text = desc_el.get_text(separator=" ", strip=True).lower()
        remote_signals = ["remote position", "remote role", "fully remote", "work remotely",
                          "uzaktan çalışma", "remote çalışma", "this is a remote"]
        hybrid_signals = ["hybrid position", "hybrid role", "hybrid work", "hibrit çalışma"]
        if any(s in desc_text for s in remote_signals):
            return "Remote"
        if any(s in desc_text for s in hybrid_signals):
            return "Hybrid"

    return ""


async def fetch_descriptions(jobs: list[RawJob]) -> dict[str, tuple[str, str]]:
    """Fetch descriptions for a batch of jobs.

    Returns {job_id: (description, work_type)}.
    """
    results: dict[str, tuple[str, str]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for job in jobs:
            desc, wtype = await _fetch_job_description(client, job.url, title=job.title)
            results[job.job_id] = (desc, wtype)
    return results
