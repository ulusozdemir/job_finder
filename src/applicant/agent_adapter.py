"""AI agent adapter using browser-use + Gemma 4 for autonomous form filling."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

MAX_AGENT_STEPS = 40
AGENT_TIMEOUT_SECONDS = 480

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
            has_session = SESSION_PATH.exists()

            import re
            normalized_url = re.sub(
                r"https?://[a-z]{2}\.linkedin\.com",
                "https://www.linkedin.com",
                url,
            )

            login_instructions = ""
            if is_linkedin:
                if has_session:
                    login_instructions = (
                        f"You are already logged in to LinkedIn (session cookies loaded).\n"
                        f"1. FIRST navigate to https://www.linkedin.com/feed to activate the session.\n"
                        f"2. If the feed loads and you see your profile/posts, you are logged in. "
                        f"Then navigate to {normalized_url} in the SAME tab (do NOT open a new tab).\n"
                        f"3. If the feed redirects to a login page, enter email: {settings.linkedin_email} "
                        f"and password: {settings.linkedin_password}, then click 'Sign in'.\n"
                        f"4. Click 'Easy Apply' if available, fill any required fields, and submit.\n"
                        f"   If there is no Easy Apply button, look for an external 'Apply' link and follow it.\n\n"
                    )
                elif settings.linkedin_email and settings.linkedin_password:
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
                f"If there are required fields you cannot fill, skip them and note them.\n\n"
                f"FORM FILLING RULES (CRITICAL):\n"
                f"- INDICES CHANGE AFTER EVERY ACTION. React/Workday re-renders the DOM after each "
                f"interaction, so element indices from a previous step are INVALID. "
                f"ALWAYS use indices from the CURRENT step's screenshot, never from memory.\n"
                f"- To fill a text field: FIRST click on the field label or the field itself in the "
                f"current screenshot to focus it. Then in the NEXT step, use input(index=<NUMBER>, "
                f"text='value', clear=true) with the index from that step's fresh screenshot.\n"
                f"- Alternatively, after filling a field, press Tab to move focus to the next field, "
                f"then type into the focused field.\n"
                f"- Do NOT use find_elements at all — it wastes steps and does NOT return interactive indices. "
                f"Read index numbers from the screenshot instead.\n"
                f"- If a field is not visible, scroll down first, then read indices from the new screenshot.\n"
                f"- AFTER FILLING A FIELD: Do NOT verify it with find_elements or any other check. "
                f"If the log says 'Typed X into element', trust it and IMMEDIATELY move to the next field. "
                f"Do NOT re-fill the same field unless the screenshot clearly shows it is empty.\n"
                f"- For ALL text input fields (name, email, phone, address, etc.), ALWAYS use the "
                f"built-in 'input' action. Do NOT use set_form_value for visible text inputs — "
                f"React overwrites DOM-set values on re-render.\n"
                f"- For radio buttons/checkboxes that don't respond to normal clicks (common on Workday): "
                f"use force_click_element(text=\"Hayır\"). Only pass text=, never selector='div'.\n"
                f"- set_form_value is ONLY for hidden inputs or native <select> elements.\n"
                f"- NEVER repeat the same failing action more than 2 times. "
                f"Try a different method or SKIP the field.\n"
                f"- If ALL methods fail for a field after 3 total attempts, SKIP it and move on.\n\n"
                f"IMPORTANT: Before retrying a field, check the screenshot first — if the field "
                f"already shows the correct value, move on. If a validation error persists, "
                f"it may be about a DIFFERENT field. Read all error messages carefully.\n\n"
                f"AUTOCOMPLETE / TYPEAHEAD FIELDS:\n"
                f"Some fields (city, country, address) require selecting from a suggestion dropdown:\n"
                f"1. Use the 'input' action to type the value (character by character).\n"
                f"2. After typing, WAIT 2-3 seconds for suggestions to appear.\n"
                f"3. Look for role='listbox' or role='option' elements and CLICK the matching one.\n"
                f"4. Do NOT press Enter — it may dismiss the dropdown without selecting.\n"
                f"5. If no dropdown appears, try variant spelling (e.g. 'İstanbul' vs 'Istanbul').\n"
                f"6. If still stuck after 3 attempts, skip the field and move on."
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

            @tools.action(description=(
                "Force-click an element using a real browser click (CDP) when normal click "
                "doesn't work. Useful for Workday and React frameworks that ignore JS events. "
                "Provide the visible text of the element to click. "
                "This generates trusted browser events that React will process."
            ))
            async def force_click_element(
                browser_session,
                text: str = "",
                selector: str = "",
            ) -> ActionResult:
                page = await browser_session.get_current_page()
                if not page:
                    return ActionResult(extracted_content="No active page found")

                find_js = """(args) => {
                    let el;
                    const t = (args.text || '').trim();
                    const sel = (args.selector || '').trim();

                    if (t) {
                        const scope = sel
                            ? [...document.querySelectorAll(sel)]
                            : [...document.querySelectorAll('*')];
                        el = scope.find(e =>
                            e.textContent.trim() === t
                            && e.offsetParent !== null
                            && e.children.length === 0
                        );
                        if (!el) el = scope.find(e =>
                            e.textContent.trim() === t
                            && e.offsetParent !== null
                        );
                    }
                    if (!el && sel && !t) {
                        const matches = document.querySelectorAll(sel);
                        if (matches.length === 1) el = matches[0];
                    }
                    if (!el) return {error: 'Element not found for text="' + t + '"'};

                    // Walk up to find the clickable radio/checkbox container
                    let target = el;
                    let radio = el.closest('[role="radio"], [role="checkbox"], [role="option"]');
                    if (!radio) {
                        for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
                            const r = p.getAttribute('role');
                            if (r === 'radio' || r === 'checkbox' || r === 'option') {
                                radio = p; break;
                            }
                            if (p.tagName === 'LABEL') {
                                const inp = p.querySelector('input');
                                if (inp) { target = inp; break; }
                            }
                        }
                    }
                    if (radio) target = radio;

                    target.scrollIntoView({block: 'center'});
                    // Small delay for scroll to settle
                    const rect = target.getBoundingClientRect();
                    return {
                        x: Math.round(rect.x + rect.width / 2),
                        y: Math.round(rect.y + rect.height / 2),
                        tag: target.tagName,
                        role: target.getAttribute('role') || 'none',
                        text: t.slice(0, 50),
                        ariaChecked: target.getAttribute('aria-checked')
                    };
                }"""
                import json as _json
                raw = await page.evaluate(find_js, {"selector": selector, "text": text})
                info = _json.loads(raw) if isinstance(raw, str) else raw
                if not info or "error" in info:
                    msg = info.get("error", "Unknown error") if info else "No result"
                    logger.warning("force_click_element: %s", msg)
                    return ActionResult(extracted_content=str(msg))

                x, y = info["x"], info["y"]
                tag = info.get("tag", "?")
                role = info.get("role", "?")
                logger.info(
                    "force_click_element: found %s[role=%s] at (%d,%d), sending CDP click",
                    tag, role, x, y,
                )

                try:
                    cdp_session = await browser_session.get_or_create_cdp_session()
                    client = cdp_session.cdp_client
                    sid = cdp_session.session_id

                    for event_type in ("mousePressed", "mouseReleased"):
                        await client.send_raw(
                            "Input.dispatchMouseEvent",
                            {
                                "type": event_type,
                                "x": x,
                                "y": y,
                                "button": "left",
                                "clickCount": 1,
                            },
                            session_id=sid,
                        )
                except Exception as e:
                    logger.warning("CDP click failed (%s), falling back to JS dispatch", e)
                    fallback_js = """(args) => {
                        const el = document.elementFromPoint(args.x, args.y);
                        if (!el) return 'No element at coordinates';
                        el.click();
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return 'JS fallback clicked: ' + el.tagName;
                    }"""
                    fb_result = await page.evaluate(fallback_js, {"x": x, "y": y})
                    return ActionResult(extracted_content=str(fb_result))

                import asyncio as _aio
                await _aio.sleep(0.3)

                verify_js = """(args) => {
                    const el = document.elementFromPoint(args.x, args.y);
                    if (!el) return 'no element at point';
                    const radio = el.closest('[role="radio"], [role="checkbox"]');
                    if (radio) return 'aria-checked=' + radio.getAttribute('aria-checked');
                    return 'clicked ' + el.tagName + ' (no aria role)';
                }"""
                verify = await page.evaluate(verify_js, {"x": x, "y": y})
                result_msg = f"CDP clicked {tag}[role={role}] '{info.get('text','')}' → {verify}"
                logger.info("force_click_element result: %s", result_msg)
                return ActionResult(extracted_content=result_msg)

            @tools.action(description=(
                "Set a form field value using JavaScript. Works for hidden inputs, "
                "custom dropdowns, and React/Angular-controlled components. "
                "Provide a CSS selector and the value to set. "
                "Use this when normal typing or force_click_element don't work."
            ))
            async def set_form_value(
                browser_session,
                selector: str,
                value: str,
            ) -> ActionResult:
                page = await browser_session.get_current_page()
                if not page:
                    return ActionResult(extracted_content="No active page found")
                js = """(args) => {
                    const el = document.querySelector(args.selector);
                    if (!el) return 'Element not found: ' + args.selector;
                    const proto = el.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : el.tagName === 'SELECT'
                            ? window.HTMLSelectElement.prototype
                            : window.HTMLInputElement.prototype;
                    const nativeSet = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (nativeSet && nativeSet.set) {
                        nativeSet.set.call(el, args.value);
                    } else {
                        el.value = args.value;
                    }
                    el.setAttribute('value', args.value);
                    el.dispatchEvent(new Event('input',  {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur',   {bubbles: true}));
                    return 'Set value to: ' + args.value + ' on ' + el.tagName;
                }"""
                result = await page.evaluate(js, {"selector": selector, "value": value})
                logger.info("set_form_value result: %s", result)
                return ActionResult(extracted_content=str(result))

            bp_kwargs: dict = dict(
                headless=settings.headless,
                extra_chromium_args=_LAUNCH_ARGS,
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            if SESSION_PATH.exists():
                bp_kwargs["storage_state"] = str(SESSION_PATH)
                bp_kwargs["user_data_dir"] = None
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
                loop_detection_enabled=True,
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
