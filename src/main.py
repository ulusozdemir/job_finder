import asyncio
import json
import logging
import sys

from google.genai.errors import ClientError

from config import settings
from src.db.database import get_session, init_db
from src.db.models import Job
from src.matcher.gemini import score_job
from src.matcher.profile import load_profile, passes_prefilter
from src.notifier.telegram import send_alert, send_job_notification
from src.scraper.linkedin import fetch_descriptions, scrape_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def run() -> None:
    logger.info("=== Job Finder Pipeline Starting ===")
    init_db()
    profile = load_profile()

    session = get_session()
    stats = {"scraped": 0, "new": 0, "prefiltered": 0, "scored": 0, "notified": 0}

    try:
        # ── Stage 0: Retry un-scored jobs from previous runs ─────
        retry_jobs = (
            session.query(Job)
            .filter(Job.passed_prefilter == True, Job.match_score == None)  # noqa: E711, E712
            .all()
        )
        if retry_jobs:
            logger.info("Found %d un-scored jobs from previous runs", len(retry_jobs))

        # ── Stage 1: Scrape ──────────────────────────────────────
        all_raw_jobs = []
        for search in profile.searches:
            logger.info("Searching: %s (%s)", search.keywords, search.location)
            jobs = await scrape_search(search)
            all_raw_jobs.extend(jobs)
            if len(all_raw_jobs) >= settings.max_jobs_per_run:
                all_raw_jobs = all_raw_jobs[:settings.max_jobs_per_run]
                break
        stats["scraped"] = len(all_raw_jobs)
        logger.info("Total scraped: %d jobs (max %d)", stats["scraped"], settings.max_jobs_per_run)

        # ── Stage 2: Dedup & Store ───────────────────────────────
        new_jobs: list[Job] = []
        for raw in all_raw_jobs:
            existing = session.query(Job).filter(Job.job_id == raw.job_id).first()
            if existing:
                continue

            job = Job(
                job_id=raw.job_id,
                title=raw.title,
                company=raw.company,
                location=raw.location,
                url=raw.url,
                posted_time=raw.posted_time,
            )
            session.add(job)
            new_jobs.append(job)

        session.commit()
        stats["new"] = len(new_jobs)
        logger.info("New jobs after dedup: %d", stats["new"])

        if not new_jobs and not retry_jobs:
            logger.info("No new jobs found. Done.")
            _log_summary(stats)
            _send_summary(stats)
            return

        if not new_jobs and retry_jobs:
            logger.info("No new jobs, but %d un-scored jobs to retry", len(retry_jobs))
            candidates = []

        # ── Stage 3: Pre-filter new jobs ─────────────────────────
        candidates: list[Job] = []
        if new_jobs:
            # 3a: Pre-filter on TITLE only (fast, no HTTP)
            title_passed: list[Job] = []
            for job in new_jobs:
                if passes_prefilter(job.title, "", profile):
                    title_passed.append(job)
                else:
                    logger.debug("Title rejected: %s @ %s", job.title, job.company)

            logger.info("Jobs passing title pre-filter: %d / %d", len(title_passed), len(new_jobs))

            if title_passed:
                # 3b: Fetch descriptions only for title survivors
                from src.scraper.linkedin import RawJob

                raw_for_desc = [
                    RawJob(
                        job_id=j.job_id,
                        title=j.title,
                        company=j.company,
                        location=j.location,
                        url=j.url,
                        posted_time=j.posted_time,
                    )
                    for j in title_passed
                ]
                logger.info("Fetching descriptions for %d jobs...", len(raw_for_desc))
                descriptions = await fetch_descriptions(raw_for_desc)

                for job in title_passed:
                    job.description = descriptions.get(job.job_id, "")

                session.commit()

                # 3c: Pre-filter on TITLE + DESCRIPTION
                for job in title_passed:
                    if passes_prefilter(job.title, job.description, profile):
                        job.passed_prefilter = True
                        candidates.append(job)
                    else:
                        logger.debug("Description rejected: %s @ %s", job.title, job.company)

                session.commit()

        stats["prefiltered"] = len(candidates)
        logger.info("Jobs passing full pre-filter: %d", stats["prefiltered"])

        if not candidates and not retry_jobs:
            logger.info("No jobs to score. Done.")
            _log_summary(stats)
            _send_summary(stats)
            return

        # ── Stage 4: AI Scoring ──────────────────────────────────
        all_to_score = retry_jobs + candidates
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY not set — skipping AI scoring")
        else:
            for i, job in enumerate(all_to_score):
                if job.match_score is not None:
                    continue
                logger.info("Scoring: %s @ %s", job.title, job.company)
                try:
                    result = score_job(
                        profile=profile,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        description=job.description,
                    )
                except ClientError:
                    logger.warning("Rate limit hit — stopping scoring, will resume next run")
                    break
                job.match_score = result["score"]
                job.match_reasons = json.dumps(result["reasons"])
                job.missing_skills = json.dumps(result["missing_skills"])
                stats["scored"] += 1

                # Stay under 10 RPM limit
                if i < len(all_to_score) - 1:
                    await asyncio.sleep(7)

            session.commit()
            logger.info("Scored %d jobs", stats["scored"])

        # ── Stage 5: Notify ──────────────────────────────────────
        all_to_notify = retry_jobs + candidates
        for job in all_to_notify:
            if job.notified:
                continue
            if job.match_score is None:
                continue
            if job.match_score < settings.score_threshold:
                logger.info(
                    "Below threshold (%d < %d): %s",
                    job.match_score, settings.score_threshold, job.title,
                )
                continue

            reasons = json.loads(job.match_reasons or "[]")
            missing = json.loads(job.missing_skills or "[]")

            sent = send_job_notification(
                title=job.title,
                company=job.company,
                location=job.location,
                url=job.url,
                score=int(job.match_score),
                reasons=reasons,
                missing_skills=missing,
            )
            if sent:
                job.notified = True
                stats["notified"] += 1

        session.commit()

        _log_summary(stats)
        _send_summary(stats)

    finally:
        session.close()


def _log_summary(stats: dict) -> None:
    logger.info("=== Pipeline Summary ===")
    logger.info("  Scraped:      %d", stats["scraped"])
    logger.info("  New (deduped): %d", stats["new"])
    logger.info("  Pre-filtered:  %d", stats["prefiltered"])
    logger.info("  AI scored:     %d", stats["scored"])
    logger.info("  Notified:      %d", stats["notified"])
    logger.info("========================")


def _send_summary(stats: dict) -> None:
    icon = "✅" if stats["notified"] > 0 else "📭"
    send_alert(
        f"{icon} Pipeline Summary\n\n"
        f"Scraped:       {stats['scraped']}\n"
        f"New (deduped): {stats['new']}\n"
        f"Pre-filtered:  {stats['prefiltered']}\n"
        f"AI scored:     {stats['scored']}\n"
        f"Notified:      {stats['notified']}"
    )


if __name__ == "__main__":
    asyncio.run(run())
