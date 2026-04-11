"""One-time script: open a browser, let you log in to LinkedIn manually,
then save the session (cookies + localStorage) to a file.

Usage:
    python save_linkedin_session.py

The browser will open LinkedIn. Log in manually (handle any CAPTCHA/verification
yourself). Once you see the LinkedIn feed, press Enter in the terminal to save.
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from src.applicant.stealth import _LAUNCH_ARGS, _STEALTH_JS, _USER_AGENT

SESSION_PATH = Path("linkedin_session.json")


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=_LAUNCH_ARGS,
        )
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Europe/Istanbul",
            color_scheme="light",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
            },
        )
        await context.add_init_script(_STEALTH_JS)

        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login")

        print("\n" + "=" * 60)
        print("Browser opened. Log in to LinkedIn manually.")
        print("Handle any CAPTCHA or verification yourself.")
        print("Waiting for you to reach the LinkedIn feed...")
        print("=" * 60 + "\n")

        # Wait until URL indicates successful login (feed, mynetwork, jobs, etc.)
        while True:
            await asyncio.sleep(2)
            try:
                url = page.url
                if any(p in url for p in ["/feed", "/mynetwork", "/jobs", "/messaging", "/in/"]):
                    print(f"Detected logged-in page: {url}")
                    break
            except Exception:
                break

        await context.storage_state(path=str(SESSION_PATH))
        print(f"\nSession saved to {SESSION_PATH.resolve()}")
        print("This file contains your LinkedIn cookies. Keep it safe.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
