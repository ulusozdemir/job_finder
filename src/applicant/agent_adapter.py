"""AI agent adapter using browser-use + Gemma 4 for autonomous form filling."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

MAX_AGENT_STEPS = 20
AGENT_TIMEOUT_SECONDS = 300

from .base import SCREENSHOT_DIR, ApplicantProfile, ApplyResult, BaseAdapter

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

            task_prompt = (
                f"Go to {url} and fill the job application form with the following information:\n"
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
                f"If you encounter a CAPTCHA, STOP immediately and report 'CAPTCHA_BLOCKED'. "
                f"Do NOT attempt to solve captchas. "
                f"If there are required fields you cannot fill, skip them and note them."
            )

            browser_profile = BrowserProfile(headless=settings.headless)
            cv_abs = str(Path(profile.cv_path).resolve())

            SCREENSHOT_DIR.mkdir(exist_ok=True)

            agent = Agent(
                task=task_prompt,
                llm=llm,
                browser_profile=browser_profile,
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
