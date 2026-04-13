"""Rule-based adapter for Greenhouse ATS forms."""

from __future__ import annotations

import logging

from playwright.async_api import Page, async_playwright

from ..base import (
    ApplicantProfile,
    ApplyResult,
    BaseAdapter,
    get_field_value,
    match_field,
    take_screenshot,
)
from ..browser.stealth import create_stealth_context

logger = logging.getLogger(__name__)


class GreenhouseAdapter(BaseAdapter):
    name = "greenhouse"

    async def apply(self, url: str, profile: ApplicantProfile) -> ApplyResult:
        async with async_playwright() as pw:
            browser, context = await create_stealth_context(pw)
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await take_screenshot(page, "greenhouse", "form_page")
                result = await self._fill_form(page, profile)
                return result
            except Exception as e:
                await take_screenshot(page, "greenhouse", "error")
                return ApplyResult(success=False, message=f"Greenhouse error: {e}", adapter_used=self.name)
            finally:
                await browser.close()

    async def _fill_form(self, page: Page, profile: ApplicantProfile) -> ApplyResult:
        filled = 0

        # Standard Greenhouse field IDs
        gh_fields = {
            "#first_name": profile.first_name,
            "#last_name": profile.last_name,
            "#email": profile.email,
            "#phone": profile.phone,
            "#location": profile.location,
        }

        for selector, value in gh_fields.items():
            el = page.locator(selector)
            if await el.count() > 0 and value:
                await el.fill(value)
                filled += 1

        # LinkedIn URL field (various selectors used by Greenhouse)
        linkedin_selectors = [
            'input[name*="linkedin"]',
            'input[id*="linkedin"]',
            'input[placeholder*="LinkedIn"]',
        ]
        for sel in linkedin_selectors:
            el = page.locator(sel)
            if await el.count() > 0:
                await el.first.fill(profile.linkedin_url)
                filled += 1
                break

        # CV upload
        resume_input = page.locator('input[type="file"]')
        if await resume_input.count() > 0 and profile.cv_path:
            await resume_input.first.set_input_files(profile.cv_path)
            filled += 1

        # Dynamic fields via label matching
        labels = page.locator("label")
        label_count = await labels.count()
        for i in range(label_count):
            label = labels.nth(i)
            text = (await label.inner_text()).strip()
            for_attr = await label.get_attribute("for")
            if not for_attr or not text:
                continue

            field_key = match_field(text)
            if not field_key:
                continue

            target = page.locator(f"#{for_attr}")
            if await target.count() > 0:
                tag = await target.evaluate("el => el.tagName.toLowerCase()")
                if tag in ("input", "textarea"):
                    val = get_field_value(field_key, profile)
                    if val:
                        await target.fill(val)
                        filled += 1

        if filled == 0:
            await take_screenshot(page, "greenhouse", "no_fields_filled")
            return ApplyResult(success=False, message="Could not fill any fields", adapter_used=self.name)

        await take_screenshot(page, "greenhouse", "form_filled")

        pre_captcha = page.locator('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], [class*="captcha"]')
        if await pre_captcha.count() > 0:
            logger.warning("Captcha present before submit — skipping submission")
            await take_screenshot(page, "greenhouse", "captcha_detected")
            return ApplyResult(success=False, message=f"Captcha detected ({filled} fields filled)", adapter_used=self.name)

        submit_btn = page.locator('button[type="submit"]:visible, input[type="submit"]:visible, #submit_app')
        if await submit_btn.count() > 0:
            await submit_btn.first.click()
            await page.wait_for_timeout(5000)
            await take_screenshot(page, "greenhouse", "after_submit")

            confirmation = page.locator('text="Application submitted"', 'text="Thank you"', '#application_confirmation')
            captcha = page.locator('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], [class*="captcha"]')

            if await confirmation.count() > 0:
                logger.info("Greenhouse form submitted and confirmed (%d fields filled)", filled)
                return ApplyResult(success=True, message=f"Submitted ({filled} fields)", adapter_used=self.name)

            if await captcha.count() > 0:
                logger.warning("Captcha detected after submit — submission likely blocked")
                return ApplyResult(success=False, message=f"Captcha blocked submission ({filled} fields filled)", adapter_used=self.name)

            if "thank" in page.url.lower() or "confirmation" in page.url.lower():
                logger.info("Greenhouse form submitted (URL confirms, %d fields filled)", filled)
                return ApplyResult(success=True, message=f"Submitted ({filled} fields)", adapter_used=self.name)

            logger.warning("Submit clicked but no confirmation detected")
            return ApplyResult(success=False, message=f"No confirmation after submit ({filled} fields filled)", adapter_used=self.name)

        return ApplyResult(
            success=False,
            message=f"Filled {filled} fields but no submit button found",
            adapter_used=self.name,
        )
