"""LinkedIn adapter: email/password login, Easy Apply, external application redirect."""

from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import Browser, Page, async_playwright

from config import settings

from .base import ApplicantProfile, ApplyResult, BaseAdapter, take_screenshot

logger = logging.getLogger(__name__)

_ANTI_DETECT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _random_delay(lo: float = 2.0, hi: float = 5.0) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _login(page: Page) -> bool:
    """Log into LinkedIn with email/password."""
    try:
        await page.goto("https://www.linkedin.com/login", wait_until="load", timeout=30000)
        await _random_delay(3, 5)
        await take_screenshot(page, "linkedin", "login_page")

        # LinkedIn has multiple login page variants; wait for any visible input
        email_selectors = [
            'input#username',
            'input[name="session_key"]',
            'input[autocomplete="username"]',
            'input[type="email"]',
            'input[type="text"]',
        ]
        pass_selectors = [
            'input#password',
            'input[name="session_password"]',
            'input[autocomplete="current-password"]',
            'input[type="password"]',
        ]

        email_input = None
        for sel in email_selectors:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=3000)
                email_input = loc
                break
            except Exception:
                continue

        if not email_input:
            await take_screenshot(page, "linkedin", "no_email_input")
            logger.error("Could not find email input on login page")
            return False

        pass_input = None
        for sel in pass_selectors:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=3000)
                pass_input = loc
                break
            except Exception:
                continue

        if not pass_input:
            await take_screenshot(page, "linkedin", "no_pass_input")
            logger.error("Could not find password input on login page")
            return False

        await email_input.fill(settings.linkedin_email)
        await _random_delay(0.5, 1.5)
        await pass_input.fill(settings.linkedin_password)
        await _random_delay(0.5, 1.0)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await _random_delay(3, 5)

        await take_screenshot(page, "linkedin", "after_login")

        captcha = page.locator('iframe[src*="captcha"], iframe[src*="challenge"], #captcha, [class*="captcha"]')
        if await captcha.count() > 0:
            logger.warning("Captcha detected on LinkedIn login")
            await take_screenshot(page, "linkedin", "login_captcha")
            return False

        if "/feed" in page.url or "/mynetwork" in page.url or "/jobs" in page.url:
            logger.info("LinkedIn login successful")
            return True

        if "challenge" in page.url or "checkpoint" in page.url:
            logger.warning("LinkedIn security challenge detected — login blocked")
            await take_screenshot(page, "linkedin", "challenge_blocked")
            return False

        logger.warning("Unexpected post-login URL: %s", page.url)
        await take_screenshot(page, "linkedin", "unexpected_url")
        return "/login" not in page.url
    except Exception as e:
        logger.error("LinkedIn login failed: %s", e)
        await take_screenshot(page, "linkedin", "login_error")
        return False


async def _try_easy_apply(page: Page, profile: ApplicantProfile) -> ApplyResult:
    """Attempt LinkedIn Easy Apply on the current job page."""
    try:
        easy_btn = page.locator('[aria-label*="Easy Apply"]')
        if await easy_btn.count() == 0:
            await take_screenshot(page, "linkedin", "easy_apply_not_found")
            return ApplyResult(success=False, message="No Easy Apply button found")

        await easy_btn.first.click()
        await _random_delay(2, 4)

        captcha_sel = 'iframe[src*="captcha"], iframe[src*="challenge"], #captcha, [class*="captcha"]'

        for _ in range(10):
            if await page.locator(captcha_sel).count() > 0:
                await take_screenshot(page, "linkedin", "easy_apply_captcha")
                return ApplyResult(success=False, message="Captcha in Easy Apply flow")

            next_btn = page.locator('button[aria-label="Continue to next step"]')
            review_btn = page.locator('button[aria-label="Review your application"]')
            submit_btn = page.locator('button[aria-label="Submit application"]')

            if await submit_btn.count() > 0:
                await submit_btn.first.click()
                await _random_delay(2, 4)

                if await page.locator(captcha_sel).count() > 0:
                    await take_screenshot(page, "linkedin", "easy_apply_captcha_after_submit")
                    return ApplyResult(success=False, message="Captcha after Easy Apply submit")

                logger.info("Easy Apply submitted")
                return ApplyResult(success=True, message="Easy Apply submitted", adapter_used="linkedin_easy_apply")

            if await review_btn.count() > 0:
                await review_btn.first.click()
                await _random_delay(1, 2)
                continue

            if await next_btn.count() > 0:
                await next_btn.first.click()
                await _random_delay(1, 2)
                continue

            break

        return ApplyResult(success=False, message="Easy Apply flow did not complete")
    except Exception as e:
        return ApplyResult(success=False, message=f"Easy Apply error: {e}")


class LinkedInAdapter(BaseAdapter):
    name = "linkedin"

    async def apply(self, url: str, profile: ApplicantProfile) -> ApplyResult:
        if not settings.linkedin_email or not settings.linkedin_password:
            return ApplyResult(success=False, message="LinkedIn credentials not configured")

        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.launch(
                headless=settings.headless,
                args=_ANTI_DETECT_ARGS,
            )
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()

            try:
                logged_in = await _login(page)
                if not logged_in:
                    captcha = page.locator('iframe[src*="captcha"], iframe[src*="challenge"], #captcha, [class*="captcha"]')
                    if await captcha.count() > 0:
                        return ApplyResult(success=False, message="Captcha on LinkedIn login")
                    return ApplyResult(success=False, message="LinkedIn login failed")

                await page.goto(url, wait_until="domcontentloaded")
                await _random_delay(3, 5)
                await take_screenshot(page, "linkedin", "job_page")

                captcha = page.locator('iframe[src*="captcha"], iframe[src*="challenge"], #captcha, [class*="captcha"]')
                if await captcha.count() > 0:
                    await take_screenshot(page, "linkedin", "job_page_captcha")
                    return ApplyResult(success=False, message="Captcha on LinkedIn job page")

                # Check for Easy Apply (LinkedIn renders it as <a> with aria-label)
                easy_btn = page.locator('[aria-label*="Easy Apply"]')
                if await easy_btn.count() > 0:
                    return await _try_easy_apply(page, profile)

                # Check for external application link
                apply_link = page.locator('a[href*="externalApply"], a[aria-label*="Apply"]')
                if await apply_link.count() > 0:
                    href = await apply_link.first.get_attribute("href")
                    if href:
                        await take_screenshot(page, "linkedin", "external_redirect")
                        return ApplyResult(
                            success=False,
                            message=f"external:{href}",
                            adapter_used="linkedin_external",
                        )

                await take_screenshot(page, "linkedin", "no_apply_method")
                return ApplyResult(success=False, message="No apply method found on page")
            finally:
                await browser.close()
