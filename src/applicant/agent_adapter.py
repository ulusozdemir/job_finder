"""AI agent adapter using browser-use + Gemma 4 for autonomous form filling."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

MAX_AGENT_STEPS = 25
AGENT_TIMEOUT_SECONDS = 300

from .base import SCREENSHOT_DIR, ApplicantProfile, ApplyResult, BaseAdapter
from .email_verifier import fetch_linkedin_verification_code
from .stealth import SESSION_PATH, _LAUNCH_ARGS, _USER_AGENT

logger = logging.getLogger(__name__)


class AgentAdapter(BaseAdapter):
    name = "agent"

    async def apply(self, url: str, profile: ApplicantProfile) -> ApplyResult:
        try:
            from browser_use import Agent, BrowserProfile
            from browser_use.llm import ChatGoogle

            from config import settings

            llm = ChatGoogle(
                model=settings.gemini_model,
                api_key=settings.gemini_api_key,
            )

            is_linkedin = "linkedin.com" in url.lower()

            login_instructions = ""
            if is_linkedin and settings.linkedin_email and settings.linkedin_password:
                login_instructions = (
                    f"IMPORTANT — LinkedIn requires login before applying.\n"
                    f"1. First go to https://www.linkedin.com/login\n"
                    f"2. Enter email: {settings.linkedin_email}\n"
                    f"3. Enter password: {settings.linkedin_password}\n"
                    f"4. Click 'Sign in' and wait for the page to load.\n"
                    f"5. If you see a CAPTCHA on the login page, STOP and report 'CAPTCHA_BLOCKED'.\n"
                    f"6. After successful login, navigate to {url}\n"
                    f"7. Click 'Easy Apply' if available, fill any required fields, and submit.\n"
                    f"   If there is no Easy Apply button, look for an external 'Apply' link and follow it.\n\n"
                )

            apply_instructions = (
                f"{'Go to ' + url + ' and f' if not is_linkedin else 'F'}ill the job application form "
                f"with the following information:\n"
            )

            task_prompt = (
                f"{login_instructions}"
                f"{apply_instructions}"
                f"- Full Name: {profile.full_name}\n"
                f"- First Name: {profile.first_name}\n"
                f"- Last Name: {profile.last_name}\n"
                f"- Email: {profile.email}\n"
                f"- Phone: {profile.phone}\n"
                f"- LinkedIn: {profile.linkedin_url}\n"
                f"- Location: {profile.location}\n"
                f"- Education: {profile.education}\n"
                f"- University: {profile.university}\n"
                f"- Years of Experience: {profile.experience_years}\n"
                f"- Salary Expectation: {profile.salary_expectation}\n"
                f"- English Proficiency: {profile.english_proficiency}\n"
                f"- Nationality: {profile.nationality}\n"
                f"- Gender: {profile.gender}\n"
                f"- Date of Birth: {profile.date_of_birth}\n"
                f"- Military Status: {profile.military_status}\n"
                f"- Work Authorization: {profile.work_authorization}\n"
                f"- Notice Period: {profile.notice_period}\n"
                f"- Willing to Relocate: {profile.willing_to_relocate}\n"
                f"- Work Mode Preference: {profile.work_mode_preference}\n"
                f"- How did you hear about us: {profile.hear_about_us}\n"
                f"\nIf salary is asked in USD, EUR, or another currency, convert from the TL amount "
                f"using approximate current exchange rates. For example 200000 TL ~ $5200 USD ~ 4800 EUR.\n"
                f"For English proficiency questions, pick the closest option to C1: "
                f"'Professional working proficiency', 'Full professional proficiency', 'Advanced', or 'Fluent'.\n"
                f"For work authorization: authorized to work in Turkey without sponsorship. "
                f"For other countries, visa sponsorship is required.\n"
                f"\nUpload the CV/resume file from: {profile.cv_path}\n"
                f"\nFor any custom or open-ended questions, answer based on this professional summary:\n"
                f"{profile.summary}\n"
                f"\nSkills: {', '.join(profile.skills[:15])}\n"
                f"\nAfter filling all fields, submit the form. "
                f"IMPORTANT security check handling:\n"
                f"- If LinkedIn shows 'Let\\'s do a quick security check' or 'Hızlıca bir güvenlik kontrolü', "
                f"look for a button to click or press-and-hold. Try interacting with it.\n"
                f"- If LinkedIn asks for an email verification code, call the "
                f"'get_linkedin_verification_code' action to retrieve it from email, "
                f"then enter the code and submit.\n"
                f"- Only report 'CAPTCHA_BLOCKED' if you see an actual image puzzle, "
                f"distorted text, or an iframe CAPTCHA that cannot be clicked through.\n"
                f"- Do NOT report CAPTCHA_BLOCKED just because you see a security check page. "
                f"Try to complete it first.\n"
                f"Do NOT keep retrying the same action if it triggers a login popup — report failure instead. "
                f"If there are required fields you cannot fill, skip them and note them."
            )

            from browser_use import ActionResult, Tools

            tools = Tools()

            @tools.action(description=(
                "Fetch the LinkedIn email verification code. "
                "Call this when LinkedIn asks for a verification code sent to email. "
                "Returns the 6-digit code or an error message."
            ))
            async def get_linkedin_verification_code() -> ActionResult:
                logger.info("Agent requested LinkedIn verification code via IMAP")
                code = await asyncio.to_thread(fetch_linkedin_verification_code)
                if code:
                    return ActionResult(extracted_content=f"Verification code: {code}")
                return ActionResult(
                    extracted_content="Could not fetch verification code from email. Report CAPTCHA_BLOCKED.",
                    error="Verification code not found",
                )

            bp_kwargs: dict = dict(
                headless=settings.headless,
                extra_chromium_args=_LAUNCH_ARGS,
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            if SESSION_PATH.exists():
                bp_kwargs["storage_state"] = str(SESSION_PATH)
                logger.info("Loading saved LinkedIn session for agent")

            browser_profile = BrowserProfile(**bp_kwargs)
            cv_abs = str(Path(profile.cv_path).resolve())

            SCREENSHOT_DIR.mkdir(exist_ok=True)

            agent = Agent(
                task=task_prompt,
                llm=llm,
                browser_profile=browser_profile,
                tools=tools,
                available_file_paths=[cv_abs],
                save_conversation_path=str(SCREENSHOT_DIR / "agent_conversation.json"),
                max_steps=MAX_AGENT_STEPS,
            )
            result = await asyncio.wait_for(
                agent.run(), timeout=AGENT_TIMEOUT_SECONDS
            )

            final_result = result.final_result() if hasattr(result, "final_result") else str(result)
            logger.info("Agent completed: %s", final_result)

            result_text = str(final_result).lower()

            if "captcha" in result_text:
                return ApplyResult(
                    success=False,
                    message="captcha detected by agent",
                    adapter_used=self.name,
                )

            is_success = not any(
                kw in result_text
                for kw in ["error", "failed", "could not", "unable"]
            )

            return ApplyResult(
                success=is_success,
                message=str(final_result)[:500],
                adapter_used=self.name,
            )
        except asyncio.TimeoutError:
            logger.error("Agent timed out after %ds", AGENT_TIMEOUT_SECONDS)
            return ApplyResult(
                success=False,
                message=f"Agent timed out ({AGENT_TIMEOUT_SECONDS}s)",
                adapter_used=self.name,
            )
        except ImportError:
            return ApplyResult(
                success=False,
                message="browser-use or langchain-google-genai not installed",
                adapter_used=self.name,
            )
        except Exception as e:
            logger.error("Agent adapter error: %s", e)
            return ApplyResult(success=False, message=f"Agent error: {e}", adapter_used=self.name)
