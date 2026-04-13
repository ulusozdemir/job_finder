"""Rule-based adapter for Lever ATS forms."""

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


class LeverAdapter(BaseAdapter):
    name = "lever"

    async def apply(self, url: str, profile: ApplicantProfile) -> ApplyResult:
        async with async_playwright() as pw:
            browser, context = await create_stealth_context(pw)
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await take_screenshot(page, "lever", "landing")
                apply_btn = page.locator('a.postings-btn, a[href*="/apply"]')
                if await apply_btn.count() > 0:
                    await apply_btn.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)

                await take_screenshot(page, "lever", "form_page")
                result = await self._fill_form(page, profile)
                return result
            except Exception as e:
                await take_screenshot(page, "lever", "error")
                return ApplyResult(success=False, message=f"Lever error: {e}", adapter_used=self.name)
            finally:
                await browser.close()

    async def _fill_form(self, page: Page, profile: ApplicantProfile) -> ApplyResult:
        filled = 0

        # Standard Lever fields
        name_input = page.locator('input[name="name"]')
        if await name_input.count() > 0:
            await name_input.fill(profile.full_name)
            filled += 1

        email_input = page.locator('input[name="email"]')
        if await email_input.count() > 0:
            await email_input.fill(profile.email)
            filled += 1

        phone_input = page.locator('input[name="phone"]')
        if await phone_input.count() > 0:
            await phone_input.fill(profile.phone)
            filled += 1

        org_input = page.locator('input[name="org"]')
        if await org_input.count() > 0:
            await org_input.fill(profile.current_company)
            filled += 1

        location_input = page.locator('input[name="location"]')
        if await location_input.count() > 0:
            await location_input.fill(profile.location)
            filled += 1

        linkedin_input = page.locator('input[name="urls[LinkedIn]"]')
        if await linkedin_input.count() > 0:
            await linkedin_input.fill(profile.linkedin_url)
            filled += 1

        # CV upload
        resume_input = page.locator('input[type="file"][name="resume"]')
        if await resume_input.count() > 0 and profile.cv_path:
            await resume_input.set_input_files(profile.cv_path)
            filled += 1

        # Dynamic custom fields via label matching
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
            await take_screenshot(page, "lever", "no_fields_filled")
            return ApplyResult(success=False, message="Could not fill any fields", adapter_used=self.name)

        await take_screenshot(page, "lever", "form_filled")

        pre_captcha = page.locator('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], [class*="captcha"]')
        if await pre_captcha.count() > 0:
            logger.warning("Captcha present before submit — skipping submission")
            await take_screenshot(page, "lever", "captcha_detected")
            return ApplyResult(success=False, message=f"Captcha detected ({filled} fields filled)", adapter_used=self.name)

        # Submit — skip hidden hCaptcha buttons, target the visible submit
        submit_btn = page.locator(
            'button[type="submit"]:visible, input[type="submit"]:visible, '
            'button:has-text("Submit Application"), button:has-text("Submit")'
        )
        if await submit_btn.count() > 0:
            await submit_btn.first.click()
            await page.wait_for_timeout(5000)
            await take_screenshot(page, "lever", "after_submit")

            # Verify submission succeeded — check for confirmation or captcha
            confirmation = page.locator('text="Application submitted"', 'text="Thank you"', '.application-confirmation')
            captcha = page.locator('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], [class*="captcha"]')

            if await confirmation.count() > 0:
                logger.info("Lever form submitted and confirmed (%d fields filled)", filled)
                return ApplyResult(success=True, message=f"Submitted ({filled} fields)", adapter_used=self.name)

            if await captcha.count() > 0:
                logger.warning("Captcha detected after submit — submission likely blocked")
                return ApplyResult(success=False, message=f"Captcha blocked submission ({filled} fields filled)", adapter_used=self.name)

            # No clear confirmation or captcha — check if URL changed (success often redirects)
            if "thanks" in page.url.lower() or "confirmation" in page.url.lower():
                logger.info("Lever form submitted (URL redirect confirms, %d fields filled)", filled)
                return ApplyResult(success=True, message=f"Submitted ({filled} fields)", adapter_used=self.name)

            logger.warning("Submit clicked but no confirmation detected")
            return ApplyResult(success=False, message=f"No confirmation after submit ({filled} fields filled)", adapter_used=self.name)

        return ApplyResult(success=False, message=f"Filled {filled} fields but no submit button found", adapter_used=self.name)
