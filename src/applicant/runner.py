"""Orchestrator: poll Telegram -> pick adapter -> apply -> report result."""

from __future__ import annotations

import asyncio
import logging
import random
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from config import settings
from src.db.database import get_session, init_db
from src.db.models import Job
from src.notifier.telegram import send_alert

from .agent_adapter import AgentAdapter
from .base import ApplicantProfile, ApplyResult, load_applicant_profile
from .greenhouse_adapter import GreenhouseAdapter
from .lever_adapter import LeverAdapter
from .linkedin_adapter import LinkedInAdapter
from .telegram_poll import answer_callback, get_pending_applications

logger = logging.getLogger(__name__)

_ADAPTERS = {
    "linkedin": LinkedInAdapter(),
    "lever": LeverAdapter(),
    "greenhouse": GreenhouseAdapter(),
    "agent": AgentAdapter(),
}


def _unwrap_linkedin_redirect(url: str) -> str:
    """Extract the real destination URL from LinkedIn's /safety/go/ redirect wrapper."""
    parsed = urlparse(url)
    if "linkedin.com" in parsed.netloc and "/safety/go" in parsed.path:
        qs = parse_qs(parsed.query)
        real_urls = qs.get("url", [])
        if real_urls:
            return real_urls[0]
    return url


def _pick_adapter(url: str) -> str:
    """Choose the best adapter based on the URL domain."""
    url_lower = url.lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "lever.co" in url_lower or "jobs.lever.co" in url_lower:
        return "lever"
    if "greenhouse.io" in url_lower or "boards.greenhouse.io" in url_lower:
        return "greenhouse"
    return "agent"


async def _apply_to_job(
    job: Job, profile: ApplicantProfile, session
) -> ApplyResult:
    """Run the appropriate adapter. Falls back to agent if rule-based fails."""
    target_url = job.url
    adapter_key = _pick_adapter(target_url)
    adapter = _ADAPTERS[adapter_key]
    logger.info("Applying to '%s' @ %s via %s", job.title, job.company, adapter.name)

    result = await adapter.apply(target_url, profile)

    # Handle LinkedIn external redirect -> re-route to the right adapter
    if not result.success and result.message.startswith("external:"):
        external_url = result.message.split("external:", 1)[1]
        target_url = _unwrap_linkedin_redirect(external_url)
        logger.info("External redirect to: %s", target_url)
        adapter_key = _pick_adapter(target_url)
        adapter = _ADAPTERS[adapter_key]
        logger.info("Re-routing to %s adapter", adapter.name)
        result = await adapter.apply(target_url, profile)

    # Fallback to AI agent if rule-based adapter failed
    if not result.success and adapter_key != "agent":
        logger.info("Rule-based adapter failed, falling back to AI agent")
        agent = _ADAPTERS["agent"]
        result = await agent.apply(target_url, profile)

    # After all attempts, if captcha is the final result, mark accordingly
    if not result.success and "captcha" in result.message.lower():
        job.apply_status = "captcha"
        session.commit()
        return ApplyResult(
            success=False,
            message=f"captcha:{target_url}",
            adapter_used=result.adapter_used,
        )

    # Update DB
    if result.success:
        job.apply_status = "applied"
        job.applied_at = datetime.now(timezone.utc)
    else:
        job.apply_status = "failed"
    session.commit()

    return result


async def run_applicant() -> None:
    """Main entry point: poll for approved applications and process them."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    logger.info("=== Auto-Apply Runner Starting ===")
    init_db()
    profile = load_applicant_profile()
    session = get_session()

    pending = get_pending_applications()
    if not pending:
        logger.info("No pending applications from Telegram")
        session.close()
        return

    # Check daily limit
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    applied_today = (
        session.query(Job)
        .filter(Job.apply_status == "applied", Job.applied_at >= today_start)
        .count()
    )
    remaining_budget = max(0, settings.max_daily_applications - applied_today)

    if remaining_budget == 0:
        logger.warning("Daily application limit reached (%d)", settings.max_daily_applications)
        for item in pending:
            answer_callback(item["callback_query_id"], "Daily limit reached, try tomorrow!")
        session.close()
        return

    logger.info(
        "Processing %d applications (budget: %d/%d)",
        min(len(pending), remaining_budget),
        remaining_budget,
        settings.max_daily_applications,
    )

    applied_count = 0
    try:
        for item in pending:
            if applied_count >= remaining_budget:
                answer_callback(item["callback_query_id"], "Daily limit reached!")
                continue

            job_id = item["job_id"]
            job = session.query(Job).filter(Job.job_id == job_id).first()
            if not job:
                logger.warning("Job not found in DB: %s", job_id)
                answer_callback(item["callback_query_id"], "Job not found in database")
                continue

            if job.apply_status == "applied":
                logger.info("Already applied to: %s", job.title)
                answer_callback(item["callback_query_id"], "Already applied!")
                continue

            if job.apply_status == "captcha":
                logger.info("Captcha-blocked, skipping: %s", job.title)
                answer_callback(item["callback_query_id"], "Captcha — apply manually")
                continue

            answer_callback(item["callback_query_id"], "Applying now...")

            result = await _apply_to_job(job, profile, session)
            applied_count += 1

            job_url = job.url or ""

            if result.success:
                logger.info("Applied: %s @ %s via %s", job.title, job.company, result.adapter_used)
                send_alert(
                    f"\u2705 Applied successfully!\n\n"
                    f"{job.title} @ {job.company}\n"
                    f"via {result.adapter_used}",
                    buttons=[[{"text": "\U0001f517 View Job", "url": job_url}]] if job_url else None,
                )
            elif result.message.startswith("captcha:"):
                captcha_url = result.message.split("captcha:", 1)[1]
                logger.warning("Captcha blocked: %s @ %s", job.title, job.company)
                send_alert(
                    f"\U0001f512 Captcha detected\n\n"
                    f"{job.title} @ {job.company}\n"
                    f"Form filled but captcha blocked submission.",
                    buttons=[[{"text": "\U0001f4dd Apply Manually", "url": captcha_url}]],
                )
            else:
                logger.warning("Failed: %s @ %s - %s", job.title, job.company, result.message)
                send_alert(
                    f"\u274c Application failed\n\n"
                    f"{job.title} @ {job.company}\n"
                    f"{result.message[:200]}",
                    buttons=[[{"text": "\U0001f517 View Job", "url": job_url}]] if job_url else None,
                )

            # Random delay between applications for ban prevention
            if applied_count < remaining_budget and applied_count < len(pending):
                delay = random.uniform(30, 90)
                logger.info("Waiting %.0fs before next application...", delay)
                await asyncio.sleep(delay)

    finally:
        session.close()

    logger.info("=== Auto-Apply Runner Finished (%d applied) ===", applied_count)


if __name__ == "__main__":
    asyncio.run(run_applicant())
