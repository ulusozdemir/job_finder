"""LinkedIn adapter: email/password login, Easy Apply, external application redirect."""

from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import Page, async_playwright

from config import settings

from .base import ApplicantProfile, ApplyResult, BaseAdapter, take_screenshot
from .email_verifier import fetch_linkedin_verification_code
from .stealth import create_stealth_context

logger = logging.getLogger(__name__)


async def _random_delay(lo: float = 2.0, hi: float = 5.0) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _login(page: Page) -> bool:
    """Log into LinkedIn with email/password."""
    try:
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
        await _random_delay(2, 4)
        await take_screenshot(page, "linkedin", "login_page")

        _EMAIL_SEL = (
            'input#username, input[name="session_key"], '
            'input[autocomplete="username"], input[type="email"]'
        )
        _PASS_SEL = (
            'input#password, input[name="session_password"], '
            'input[autocomplete="current-password"], input[type="password"]'
        )

        try:
            await page.wait_for_selector(_EMAIL_SEL, state="visible", timeout=15000)
        except Exception:
            await take_screenshot(page, "linkedin", "no_email_input")
            logger.error("Could not find email input on login page")
            return False

        email_input = page.locator(_EMAIL_SEL).first
        pass_input = page.locator(_PASS_SEL).first

        await email_input.click()
        await _random_delay(0.3, 0.6)
        await email_input.fill(settings.linkedin_email)
        await _random_delay(0.5, 1.5)
        await pass_input.click()
        await _random_delay(0.3, 0.6)
        await pass_input.fill(settings.linkedin_password)
        await _random_delay(0.5, 1.0)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await _random_delay(3, 5)

        await take_screenshot(page, "linkedin", "after_login")

        if "/feed" in page.url or "/mynetwork" in page.url or "/jobs" in page.url:
            logger.info("LinkedIn login successful")
            return True

        # Email verification challenge (check BEFORE captcha -- the page
        # may contain "challenge" iframes that are NOT captchas)
        if "checkpoint" in page.url or "challenge" in page.url:
            await take_screenshot(page, "linkedin", "verification_page")
            code_input = page.locator('input#input__email_verification_pin, input[name="pin"]')
            if await code_input.count() > 0:
                logger.info("Email verification required, fetching code via IMAP...")
                code = await asyncio.to_thread(fetch_linkedin_verification_code)
                if code:
                    await code_input.first.fill(code)
                    await _random_delay(0.5, 1.0)
                    submit = page.locator('button#email-pin-submit-button, button[type="submit"]')
                    if await submit.count() > 0:
                        await submit.first.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=30000)
                        await _random_delay(3, 5)
                        await take_screenshot(page, "linkedin", "after_verification")

                        if "/feed" in page.url or "/mynetwork" in page.url or "/jobs" in page.url:
                            logger.info("LinkedIn login successful after email verification")
                            return True

                        logger.warning("Verification submitted but login not confirmed: %s", page.url)
                        return "/login" not in page.url
                else:
                    logger.warning("Could not fetch verification code from email")
                    return False

        # Real CAPTCHA detection (narrow selectors -- exclude "challenge" iframes)
        captcha = page.locator('iframe[src*="captcha"], iframe[src*="hcaptcha"], iframe[src*="recaptcha"], #captcha, .captcha')
        if await captcha.count() > 0:
            logger.warning("Captcha detected on LinkedIn login")
            await take_screenshot(page, "linkedin", "login_captcha")
            return False

        # Generic security challenge without email verification input
        if "checkpoint" in page.url or "challenge" in page.url:
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
            browser, context = await create_stealth_context(pw)
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
