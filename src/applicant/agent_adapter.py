"""AI agent adapter using browser-use + Gemma 4 for autonomous form filling."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

MAX_AGENT_STEPS = 65
AGENT_TIMEOUT_SECONDS = 780

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

            closed_job_instructions = (
                f"\nJOB CLOSED / ALREADY APPLIED DETECTION (CRITICAL):\n"
                f"After navigating to the job page, if you see ANY of these:\n"
                f"- 'No longer accepting applications'\n"
                f"- 'This job is no longer available'\n"
                f"- 'This position has been filled'\n"
                f"- 'Bu ilan artık aktif değil'\n"
                f"- 'Başvuru kabul edilmiyor'\n"
                f"- 'Applied X ago' / 'Application submitted'\n"
                f"- 'Başvurunuz gönderildi' / 'Başvurdunuz'\n"
                f"Then IMMEDIATELY use the done action with message: 'JOB_CLOSED: <reason>.'\n"
                f"Do NOT search for apply buttons, do NOT scroll, do NOT extract links. Just report done.\n"
            )

            task_prompt = (
                f"{login_instructions}"
                f"{closed_job_instructions}"
                f"{apply_instructions}"
                f"- Full Name: {profile.full_name}\n"
                f"- First Name: {profile.first_name}\n"
                f"- Last Name: {profile.last_name}\n"
                f"- Email: {profile.email}\n"
                f"- Phone (local, without country code): {profile.phone}\n"
                f"- LinkedIn: {profile.linkedin_url}\n"
                f"- Location: {profile.location}\n"
                f"- Address / Cadde/Sokak Adı: {profile.address_line}\n"
                f"- City / Şehir: {profile.city}\n"
                f"- District / Mahalle/Köy / İlçe: {profile.district}\n"
                f"- Postal Code / Posta Kodu: {profile.postal_code}\n"
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
                f"\nPHONE NUMBER: The phone number above is the LOCAL number without country code. "
                f"If the form has a separate 'Ülke Telefon Kodu' / country code dropdown already set to "
                f"Türkiye (+90), enter ONLY the local number (e.g. 5077211015). "
                f"Do NOT prepend +90 or 0. If there is no separate country code field, use +90{profile.phone}.\n"
                f"\nADDRESS FIELDS: Workday has separate fields for address. Use the values above exactly. "
                f"For autocomplete fields (Mahalle/Köy, Şehir), type the value and use the "
                f"autocomplete_select tool to pick from suggestions.\n"
                f"\nIf salary is asked in USD, EUR, or another currency, convert from the TL amount "
                f"using approximate current exchange rates. For example 190000 TL ~ $4900 USD ~ 4500 EUR.\n"
                f"IMPORTANT: The salary above is NET. If the form asks for net, use 190000. "
                f"If the form asks for GROSS, convert up: write 280000 TL gross (roughly 190000 net in Turkey). "
                f"Always match the form's gross/net requirement.\n"
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
                f"- For ALL text input fields, use fill_text_field(label='Field Label', value='...'). "
                f"This is the PREFERRED method — it finds fields by label text and does NOT need element indices.\n"
                f"- For DROPDOWN / AUTOCOMPLETE fields (City, Country, Skill, Language level, etc.), "
                f"use autocomplete_select(label='Field Label', value='...'). "
                f"It types the value and clicks the matching suggestion from the dropdown.\n"
                f"- ONE-STRIKE RULE: If fill_text_field or autocomplete_select fails ONCE for a field, "
                f"do NOT retry it for that field. The tool searches by label/name in the DOM — "
                f"if it fails, retrying will always fail. Instead, IMMEDIATELY switch to:\n"
                f"  1. Use the 'input' action with the element INDEX from the CURRENT screenshot\n"
                f"  2. For dropdowns: after typing with 'input', wait 1-2 seconds, "
                f"then 'click' the matching option from the dropdown in the screenshot\n"
                f"  INDICES CHANGE AFTER EVERY ACTION on React pages — always use the latest screenshot.\n"
                f"- Do NOT use find_elements or search_page — they waste steps.\n"
                f"- AFTER FILLING A FIELD: move to the next field immediately. "
                f"Do NOT verify or re-fill unless the screenshot clearly shows it is empty.\n"
                f"- For radio buttons: use force_click_element(text=\"Hayır\"). Only pass text=, never selector='div'.\n"
                f"- For CHECKBOXES (e.g. Terms & Conditions / 'hüküm ve koşullar'): "
                f"use force_click_element(text='short unique text from the label'). "
                f"Keep the text SHORT (first 20-30 chars). Example: force_click_element(text='Evet, hüküm ve koşul'). "
                f"If force_click_element fails ONCE, immediately use evaluate with JS: "
                f"document.querySelector('[role=\"checkbox\"]').click() or "
                f"document.querySelector('input[type=\"checkbox\"]').click(). "
                f"Do NOT retry force_click_element more than once for checkboxes.\n"
                f"- For native <select> dropdowns: use set_form_value(selector, value).\n"
                f"- NEVER repeat the same failing action for the same field. "
                f"Always switch strategy after 1 failure.\n"
                f"- VALIDATION ERRORS: When you click 'İleri'/'Next' and see errors, "
                f"read EACH error message carefully. They may be about DIFFERENT fields. "
                f"Do NOT assume all errors are about the last field you edited. "
                f"Fix each specific field mentioned in the error.\n\n"
                f"AUTOCOMPLETE / TYPEAHEAD FIELDS (Mahalle/Köy, Şehir, Ülke, etc.):\n"
                f"Use the autocomplete_select tool for these fields. It types, waits for suggestions, "
                f"and clicks the best match automatically. Examples:\n"
                f"  autocomplete_select(label='Mahalle/Köy', value='Etimesgut')\n"
                f"  autocomplete_select(label='Şehir', value='Ankara')\n"
                f"If autocomplete_select fails, skip the field and move on. "
                f"Do NOT retry the same autocomplete field more than 2 times."
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
                        if (!el) el = scope.find(e =>
                            e.innerText && e.innerText.includes(t)
                            && e.offsetParent !== null
                        );
                        if (!el) {
                            const shorter = t.substring(0, 30);
                            el = scope.find(e =>
                                e.textContent.includes(shorter)
                                && e.offsetParent !== null
                                && e.children.length === 0
                            );
                        }
                        if (!el) {
                            const allEls = [...document.querySelectorAll('*')];
                            const textEl = allEls.find(e =>
                                e.innerText && e.innerText.includes(t)
                                && e.offsetParent !== null
                            );
                            if (textEl) {
                                const cb = textEl.closest('label, [role="checkbox"], [role="radio"]');
                                if (cb) el = cb;
                                else {
                                    const parent = textEl.parentElement;
                                    if (parent) {
                                        const nearCb = parent.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"]');
                                        if (nearCb) el = nearCb;
                                        else el = textEl;
                                    } else el = textEl;
                                }
                            }
                        }
                    }
                    if (!el && sel && !t) {
                        const matches = document.querySelectorAll(sel);
                        if (matches.length === 1) el = matches[0];
                    }
                    if (!el) return JSON.stringify({error: 'Element not found for text="' + t + '"'});

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
                    const rect = target.getBoundingClientRect();
                    return JSON.stringify({
                        x: Math.round(rect.x + rect.width / 2),
                        y: Math.round(rect.y + rect.height / 2),
                        tag: target.tagName,
                        role: target.getAttribute('role') || 'none',
                        text: t.slice(0, 50),
                        ariaChecked: target.getAttribute('aria-checked')
                    });
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

            _FIND_INPUT_JS = """(args) => {
                const label = (args.label || '').trim();
                const name = (args.name || '').trim();
                const labelClean = label.replace(/\\s*\\*\\s*$/, '').trim();
                const SELECTORS = 'input, textarea, select, [role="combobox"]';
                let input;

                function findNearInput(el) {
                    if (!el) return null;
                    let found = el.querySelector(SELECTORS);
                    if (found) return found;
                    let sib = el.nextElementSibling;
                    for (let i = 0; i < 5 && sib; i++) {
                        found = sib.querySelector(SELECTORS)
                            || (sib.matches(SELECTORS) ? sib : null);
                        if (found) return found;
                        sib = sib.nextElementSibling;
                    }
                    const parent = el.closest(
                        '.form-group, [class*="field"], [class*="Field"], ' +
                        '[data-automation-id], [class*="container"], [class*="wrapper"]'
                    );
                    if (parent) {
                        found = parent.querySelector(SELECTORS);
                        if (found) return found;
                    }
                    return null;
                }

                function textMatch(text) {
                    const t = text.trim().replace(/\\s*\\*\\s*$/, '');
                    return t === label || t === labelClean
                        || t.includes(label) || t.includes(labelClean);
                }

                if (label) {
                    const labels = [...document.querySelectorAll('label')];
                    const match = labels.find(l => textMatch(l.textContent));
                    if (match) {
                        const forId = match.getAttribute('for');
                        if (forId) input = document.getElementById(forId);
                        if (!input) input = findNearInput(match);
                    }
                    if (!input) {
                        const allText = [...document.querySelectorAll(
                            'span, div, p, h3, h4, strong, legend'
                        )];
                        const textEl = allText.find(el =>
                            textMatch(el.textContent) && el.children.length < 3
                        );
                        if (textEl) input = findNearInput(textEl);
                    }
                    if (!input) input = document.querySelector(
                        `[aria-label*="${labelClean}"]`
                    );
                    if (input && !input.matches(SELECTORS)) {
                        const inner = input.querySelector(SELECTORS);
                        if (inner) input = inner;
                    }
                    if (!input) input = document.querySelector(
                        `input[placeholder*="${labelClean}"], textarea[placeholder*="${labelClean}"], ` +
                        `[role="combobox"][placeholder*="${labelClean}"]`
                    );
                }
                if (!input && name) {
                    input = document.querySelector(
                        `input[name*="${name}"], textarea[name*="${name}"], ` +
                        `[role="combobox"][name*="${name}"]`
                    );
                    if (!input) input = document.querySelector(
                        `[id*="${name}"][role="combobox"], input[id*="${name}"], textarea[id*="${name}"]`
                    );
                }
                if (!input) return JSON.stringify({found: false});
                const isCombobox = input.getAttribute('role') === 'combobox'
                    || input.classList.toString().includes('select__input');
                input.scrollIntoView({block: 'center'});
                input.focus();
                input.click();
                return JSON.stringify({
                    found: true, tag: input.tagName,
                    name: input.name || '', id: input.id || '',
                    isCombobox: isCombobox
                });
            }"""

            async def _cdp_type(browser_session, page, text):
                """Clear field and type text via CDP (trusted events)."""
                cdp_session = await browser_session.get_or_create_cdp_session()
                client = cdp_session.cdp_client
                sid = cdp_session.session_id
                await page.evaluate("() => document.execCommand('selectAll')")
                await client.send_raw(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Delete", "code": "Delete",
                     "windowsVirtualKeyCode": 46, "nativeVirtualKeyCode": 46},
                    session_id=sid,
                )
                await client.send_raw(
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": "Delete", "code": "Delete",
                     "windowsVirtualKeyCode": 46, "nativeVirtualKeyCode": 46},
                    session_id=sid,
                )
                import asyncio as _aio
                await _aio.sleep(0.1)
                await client.send_raw(
                    "Input.insertText", {"text": text}, session_id=sid,
                )

            @tools.action(description=(
                "Fill a text input field by its visible label text (e.g. 'Soyadı', 'E-posta'). "
                "Finds the field by label, clears it, and types the value via CDP (trusted events). "
                "No element index needed. PREFERRED method for ALL text input fields. "
                "If label doesn't match, pass the input's name attribute instead."
            ))
            async def fill_text_field(
                browser_session,
                label: str = "",
                name: str = "",
                value: str = "",
            ) -> ActionResult:
                page = await browser_session.get_current_page()
                if not page:
                    return ActionResult(extracted_content="No active page found")
                try:
                    import json as _json
                    raw = await page.evaluate(_FIND_INPUT_JS, {"label": label, "name": name})
                    info = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                    if not info.get("found"):
                        msg = f"Field not found: label='{label}', name='{name}'"
                        logger.warning("fill_text_field: %s", msg)
                        return ActionResult(extracted_content=msg)

                    await _cdp_type(browser_session, page, value)

                    actual = await page.evaluate("() => document.activeElement ? document.activeElement.value : ''")
                    msg = (
                        f"Filled {info.get('tag','')} (label='{label}', name='{info.get('name','')}') "
                        f"with '{value}' (actual='{actual}')"
                    )
                    logger.info("fill_text_field: %s", msg)
                    return ActionResult(extracted_content=msg)
                except Exception as e:
                    msg = f"fill_text_field error: {e}"
                    logger.warning(msg)
                    return ActionResult(extracted_content=msg)

            @tools.action(description=(
                "Type into an autocomplete/typeahead/dropdown field and click the matching suggestion. "
                "Works with React Select, Workday, Material UI, and standard HTML dropdowns. "
                "Use for fields like City, Country, Skill, English level, Notice period — "
                "any field that shows a dropdown/suggestions after typing. "
                "Finds the input by label text, types the value, waits for suggestions, and clicks the best match."
            ))
            async def autocomplete_select(
                browser_session,
                label: str = "",
                name: str = "",
                value: str = "",
            ) -> ActionResult:
                page = await browser_session.get_current_page()
                if not page:
                    return ActionResult(extracted_content="No active page found")
                try:
                    import json as _json
                    raw = await page.evaluate(_FIND_INPUT_JS, {"label": label, "name": name})
                    info = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                    if not info.get("found"):
                        msg = f"autocomplete_select: field not found: label='{label}', name='{name}'"
                        logger.warning(msg)
                        return ActionResult(extracted_content=msg)

                    import asyncio as _aio
                    if info.get("isCombobox"):
                        await page.evaluate("() => document.activeElement && document.activeElement.select()")
                        await page.keyboard.press("Delete")
                        await _aio.sleep(0.1)
                        await page.keyboard.type(value, delay=50)
                    else:
                        await _cdp_type(browser_session, page, value)

                    for wait in (1.0, 1.5):
                        await _aio.sleep(wait)
                        click_js = """(args) => {
                            const val = args.value.toLowerCase();
                            const options = document.querySelectorAll(
                                '[role="option"], [role="listbox"] li, ' +
                                'ul[role="listbox"] > li, div[data-automation-id*="promptOption"], ' +
                                '[data-automation-id*="selectOption"], ' +
                                '.css-1dimb5e-option, [class*="option"], [class*="Option"]'
                            );
                            let best = null;
                            for (const opt of options) {
                                const txt = opt.textContent.toLowerCase().trim();
                                if (txt === val) {
                                    opt.scrollIntoView({block: 'center'});
                                    opt.click();
                                    return 'Selected: ' + opt.textContent.trim();
                                }
                                if (!best && txt.includes(val)) best = opt;
                            }
                            if (best) {
                                best.scrollIntoView({block: 'center'});
                                best.click();
                                return 'Selected: ' + best.textContent.trim();
                            }
                            const allVisible = document.querySelectorAll(
                                '[role="option"], li[tabindex], div[data-automation-id] li'
                            );
                            if (allVisible.length > 0) {
                                allVisible[0].click();
                                return 'Selected first option: ' + allVisible[0].textContent.trim();
                            }
                            return 'NO_SUGGESTIONS';
                        }"""
                        result = await page.evaluate(click_js, {"value": value})
                        if result != "NO_SUGGESTIONS":
                            break

                    msg = f"autocomplete_select(label='{label}', value='{value}'): {result}"
                    logger.info(msg)

                    if result == "NO_SUGGESTIONS":
                        cdp_s = await browser_session.get_or_create_cdp_session()
                        for etype in ("keyDown", "keyUp"):
                            await cdp_s.cdp_client.send_raw(
                                "Input.dispatchKeyEvent",
                                {"type": etype, "key": "Enter", "code": "Enter",
                                 "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                                session_id=cdp_s.session_id,
                            )
                        msg += " — pressed Enter as fallback"

                    return ActionResult(extracted_content=msg)
                except Exception as e:
                    msg = f"autocomplete_select error: {e}"
                    logger.warning(msg)
                    return ActionResult(extracted_content=msg)

            @tools.action(description=(
                "Set a form field value using JavaScript. Works for hidden inputs, "
                "custom dropdowns, and native <select> elements. "
                "Provide a CSS selector and the value to set. "
                "Do NOT use for visible text inputs — use fill_text_field instead."
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

            if "job_closed" in result_text:
                reason = str(final_result).split("JOB_CLOSED:")[-1].strip()[:200] if "JOB_CLOSED:" in str(final_result) else "Job is no longer available"
                return ApplyResult(
                    success=False,
                    message=f"job_closed:{reason}",
                    adapter_used=self.name,
                )

            is_success = not any(
                kw in result_text
                for kw in ["error", "failed", "could not", "unable",
                            "already applied", "already submitted",
                            "no longer accepting", "not available"]
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
