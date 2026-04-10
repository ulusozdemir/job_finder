import asyncio
import json
import logging
import sys

from google.genai.errors import ClientError

from config import settings
from src.db.database import get_session, init_db
from src.db.models import Job
from src.matcher.gemini import score_job
from src.matcher.profile import is_blacklisted, load_profile, passes_prefilter
from src.notifier.telegram import send_alert, send_job_notification
from src.scraper.linkedin import fetch_descriptions, scrape_page

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

        # ── Stages 1-3: Scrape → Dedup → Pre-filter ────────────
        # For each search query, paginate until we find enough filtered
        # jobs or LinkedIn runs out of results (max 10 pages safety cap).
        from src.scraper.linkedin import RawJob

        MAX_PAGES = 10
        candidates: list[Job] = []
        target = settings.min_filtered_jobs

        for search in profile.searches:
            if len(candidates) >= target:
                logger.info("Reached target of %d filtered jobs, stopping", target)
                break

            logger.info("Searching: %s (%s)", search.keywords, search.location)

            for page in range(MAX_PAGES):
                if len(candidates) >= target:
                    break

                raw_jobs = await scrape_page(search, page)
                if raw_jobs is None:
                    break
                stats["scraped"] += len(raw_jobs)

                # Dedup & store
                new_jobs: list[Job] = []
                for raw in raw_jobs:
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
                stats["new"] += len(new_jobs)

                if not new_jobs:
                    logger.info("No new jobs on page %d, trying next page", page + 1)
                    continue

                # Blacklist + title pre-filter
                title_passed = [
                    j for j in new_jobs
                    if not is_blacklisted(j.company, profile)
                    and passes_prefilter(j.title, "", profile)
                ]
                logger.info(
                    "Page %d: %d new, %d passed title filter",
                    page + 1, len(new_jobs), len(title_passed),
                )

                if not title_passed:
                    continue

                # Fetch descriptions for title survivors
                raw_for_desc = [
                    RawJob(
                        job_id=j.job_id, title=j.title, company=j.company,
                        location=j.location, url=j.url, posted_time=j.posted_time,
                    )
                    for j in title_passed
                ]
                detail_results = await fetch_descriptions(raw_for_desc)
                for job in title_passed:
                    desc, wtype = detail_results.get(job.job_id, ("", ""))
                    job.description = desc
                    job.work_type = wtype
                session.commit()

                # Full pre-filter (title + description)
                for job in title_passed:
                    if passes_prefilter(job.title, job.description, profile):
                        job.passed_prefilter = True
                        candidates.append(job)
                session.commit()

                logger.info("Filtered so far: %d / %d target", len(candidates), target)

        stats["prefiltered"] = len(candidates)
        logger.info("Total scraped: %d, new: %d, filtered: %d", stats["scraped"], stats["new"], stats["prefiltered"])

        if not candidates and not retry_jobs:
            logger.info("No jobs to score. Done.")
            _log_summary(stats)
            _send_summary(stats)
            return

        # ── Stage 3.5: Content-based dedup (same title+company within N days) ──
        from datetime import datetime, timedelta, timezone
        dedup_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.dedup_days)

        def _is_duplicate(job: Job) -> bool:
            """Check if we notified a same title+company job within the dedup window."""
            normalised_title = job.title.strip().lower()
            normalised_company = job.company.strip().lower()
            return (
                session.query(Job)
                .filter(
                    Job.id != job.id,
                    Job.notified == True,  # noqa: E712
                    Job.created_at >= dedup_cutoff,
                    Job.title.ilike(normalised_title),
                    Job.company.ilike(normalised_company),
                )
                .first()
                is not None
            )

        seen_pairs: set[tuple[str, str]] = set()

        deduped_candidates: list[Job] = []
        for job in candidates:
            key = (job.title.strip().lower(), job.company.strip().lower())
            if key in seen_pairs or _is_duplicate(job):
                job.notified = True
                logger.info("Duplicate (already notified): %s @ %s", job.title, job.company)
            else:
                seen_pairs.add(key)
                deduped_candidates.append(job)
        session.commit()

        deduped_retries: list[Job] = []
        for job in retry_jobs:
            key = (job.title.strip().lower(), job.company.strip().lower())
            if key in seen_pairs or _is_duplicate(job):
                job.notified = True
                logger.info("Duplicate retry (already notified): %s @ %s", job.title, job.company)
            else:
                seen_pairs.add(key)
                deduped_retries.append(job)
        session.commit()

        logger.info(
            "Content dedup: %d candidates → %d, %d retries → %d",
            len(candidates), len(deduped_candidates),
            len(retry_jobs), len(deduped_retries),
        )

        # ── Stage 4: AI Scoring ──────────────────────────────────
        all_to_score = deduped_retries + deduped_candidates
        total_to_score = sum(1 for j in all_to_score if j.match_score is None)

        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY not set — skipping AI scoring")
        else:
            rpm_delay = 60.0 / settings.gemini_rpm + 1  # seconds between calls
            max_calls = settings.gemini_max_per_run
            scored_count = 0

            logger.info(
                "Scoring %d jobs (rate: %d RPM, delay %.0fs, cap %d/run)",
                total_to_score, settings.gemini_rpm, rpm_delay, max_calls,
            )

            for job in all_to_score:
                if job.match_score is not None:
                    continue
                if scored_count >= max_calls:
                    remaining = total_to_score - scored_count
                    logger.warning(
                        "Per-run limit reached (%d/%d). %d jobs deferred to next run.",
                        scored_count, max_calls, remaining,
                    )
                    break

                logger.info("Scoring [%d/%d]: %s @ %s", scored_count + 1, total_to_score, job.title, job.company)
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
                    session.commit()
                    break
                job.match_score = result["score"]
                job.match_reasons = json.dumps(result["reasons"])
                job.missing_skills = json.dumps(result["missing_skills"])
                job.rejection_reason = result.get("rejection_reason", "")
                session.commit()
                scored_count += 1
                stats["scored"] += 1

                if scored_count < max_calls:
                    await asyncio.sleep(rpm_delay)

            logger.info("Scored %d jobs", stats["scored"])

        # ── Stage 5: Notify ──────────────────────────────────────
        all_to_notify = deduped_retries + deduped_candidates
        below_threshold: list[Job] = []
        for job in all_to_notify:
            if job.notified:
                continue
            if job.match_score is None:
                continue
            if job.match_score < settings.score_threshold:
                below_threshold.append(job)
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
                posted_time=job.posted_time,
                work_type=job.work_type,
            )
            if sent:
                job.notified = True
                stats["notified"] += 1

        session.commit()

        _log_summary(stats)
        _send_summary(stats, below_threshold)

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


def _send_summary(stats: dict, below_threshold: list | None = None) -> None:
    icon = "✅" if stats["notified"] > 0 else "📭"
    lines = [
        f"{icon} Pipeline Summary\n",
        f"Scraped:       {stats['scraped']}",
        f"New (deduped): {stats['new']}",
        f"Pre-filtered:  {stats['prefiltered']}",
        f"AI scored:     {stats['scored']}",
        f"Notified:      {stats['notified']}",
    ]

    if below_threshold:
        lines.append(f"\n🚫 Rejected by AI (score < {settings.score_threshold}):")
        for job in below_threshold:
            reason = job.rejection_reason or "—"
            missing = json.loads(job.missing_skills or "[]")
            missing_str = ", ".join(missing[:3]) if missing else "—"
            lines.append(f"  [{int(job.match_score)}] {job.title} @ {job.company}")
            lines.append(f"       {reason}")
            lines.append(f"       Missing: {missing_str}")
            lines.append(f"       {job.url}")

    send_alert("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(run())
