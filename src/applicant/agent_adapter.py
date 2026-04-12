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
                f"If the form has a separate country code dropdown already set to the correct country, "
                f"enter ONLY the local number. Do NOT prepend the country code or leading zero. "
                f"If there is NO separate country code field, use +90{profile.phone}.\n"
                f"\nADDRESS FIELDS: Some forms split address into multiple fields. "
                f"Use the values above exactly. Do NOT assume which fields are dropdowns and which are text — "
                f"detect the type at runtime (see FIELD TYPE DETECTION below).\n"
                f"\nSALARY: The salary above is NET. If the form asks for net, use {profile.salary_expectation}. "
                f"If the form asks for GROSS, multiply by ~1.47 (Turkish tax). "
                f"If a different currency is requested, convert at approximate current rates.\n"
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
                f"- If the site shows a security check, CAPTCHA button, or press-and-hold challenge, "
                f"try to interact with it before giving up.\n"
                f"- If LinkedIn asks for an email verification code, call the "
                f"'get_linkedin_verification_code' action to retrieve it from email, "
                f"then enter the code and submit.\n"
                f"- Only report 'CAPTCHA_BLOCKED' if you see an actual image puzzle, "
                f"distorted text, or an iframe CAPTCHA that cannot be clicked through.\n"
                f"Do NOT keep retrying the same action if it triggers a login popup — report failure instead. "
                f"If there are required fields you cannot fill, skip them and note them.\n\n"

                # ── FIELD TYPE DETECTION ──
                f"FIELD TYPE DETECTION (CRITICAL — DO NOT ASSUME):\n"
                f"You do NOT know in advance whether a field is a dropdown or plain text. "
                f"ALWAYS detect the type by behavior:\n"
                f"  1. Type into the field using 'input' with clear=True.\n"
                f"  2. Look at the screenshot IMMEDIATELY after typing.\n"
                f"  3. DROPDOWN: a suggestion list / popup appeared → click the matching option.\n"
                f"  4. PLAIN TEXT: NO suggestions appeared → the value is already entered. Move on.\n"
                f"     If unsure, call fill_text_field(label=..., value=...) to confirm the value.\n"
                f"  5. NATIVE <select>: the field shows a fixed list when clicked → "
                f"use native_select(label=..., value=...).\n"
                f"This applies to ALL fields regardless of their label. Never assume a field is a dropdown "
                f"just because it sounds like one (city, district, etc.) — always let the UI tell you.\n\n"

                # ── FIELD ORDERING ──
                f"FIELD ORDERING (CRITICAL — ALWAYS FOLLOW):\n"
                f"Many forms have DEPENDENT FIELDS where a child field's options only load after "
                f"its parent is filled. ALWAYS fill fields in TOP-TO-BOTTOM, LEFT-TO-RIGHT order "
                f"as they appear on screen.\n"
                f"If a field shows NO suggestions, check whether there is a PARENT field above it "
                f"that needs to be filled first. Fill the parent, then retry the child ONCE.\n\n"

                # ── PRE-FILLED DROPDOWNS ──
                f"PRE-FILLED DROPDOWNS:\n"
                f"Some dropdown fields appear to already have a value, but the framework has NOT "
                f"registered it internally. Child fields will show no options until the parent is "
                f"actively selected. RULE: If a dropdown field already displays a value, you MUST "
                f"still click it, type the value, and select it from the suggestions. "
                f"This ensures the framework registers the selection and loads dependent options.\n\n"

                # ── FORM FILLING TOOLS ──
                f"FORM FILLING — WHICH TOOL TO USE:\n"
                f"- fill_text_field(label=..., value=...): For plain text inputs. Finds fields by label. "
                f"Also works for date, number, contenteditable, and native <select>.\n"
                f"- 'input' action + 'click': For dropdown/autocomplete/typeahead fields. "
                f"Type → see suggestions → click one. Do NOT use fill_text_field for these "
                f"(it dispatches blur which closes the dropdown).\n"
                f"- native_select(label=..., value=...): For native HTML <select> elements.\n"
                f"- set_form_value(selector, value): For hidden inputs, radios, checkboxes, sliders.\n"
                f"- force_click_element(text=...): For radio buttons and checkboxes. "
                f"ONLY pass text= with the exact visible label text. "
                f"Do NOT pass selector= for radios/checkboxes — it causes wrong element selection. "
                f"Keep text SHORT (first 20-30 chars). "
                f"If it fails once, fallback to clicking the element by index from the screenshot.\n"
                f"- upload_file: For file upload fields.\n\n"

                # ── DROPDOWN STEP-BY-STEP ──
                f"DROPDOWN FIELDS — STEP-BY-STEP:\n"
                f"  Step 1: Click the field to focus it.\n"
                f"  Step 2: Use 'input' action with clear=True to type the value.\n"
                f"  Step 3: Check the screenshot.\n"
                f"    → Suggestions appeared? Click the matching one. DONE.\n"
                f"    → NO suggestions? This is a plain text input. The value is already typed. "
                f"MOVE ON to the next field. Do NOT retry.\n\n"

                # ── ONE-STRIKE RULE ──
                f"ONE-STRIKE RULE (ABSOLUTE — NEVER VIOLATE):\n"
                f"Every field gets a MAXIMUM of 3 total actions. After 3 actions on the SAME field, SKIP it.\n"
                f"Escalation strategy:\n"
                f"  A) fill_text_field fails → try 'input' once → still fails → SKIP.\n"
                f"  B) 'input' typed, NO suggestions appeared → it is plain text, value is set. MOVE ON. "
                f"Do NOT retry 'input' hoping suggestions will appear.\n"
                f"  C) 'input' typed, suggestions appeared, click failed → retry 'input' + 'click' ONCE → "
                f"still fails → SKIP.\n"
                f"CRITICAL: If you typed into a field and no suggestions appeared, it is NOT a dropdown. "
                f"Stop. The value is already in the field. Move to the next field.\n\n"

                # ── BANNED ACTIONS ──
                f"BANNED ACTIONS (NEVER USE):\n"
                f"- find_elements — wastes steps, returns irrelevant elements.\n"
                f"- search_page — wastes steps.\n"
                f"- extract — for reading dropdown options. Just look at the screenshot.\n"
                f"- evaluate with querySelectorAll for dropdown options.\n"
                f"- Retrying the same action more than twice on one field.\n"
                f"- Using fill_text_field on a field where you saw dropdown suggestions.\n\n"

                # ── GENERAL RULES ──
                f"AFTER FILLING A FIELD: move to the next field immediately. "
                f"Do NOT verify or re-fill unless the screenshot clearly shows the field is empty.\n"
                f"INDICES CHANGE after every action on dynamic pages — always use the latest screenshot.\n\n"
                f"VALIDATION ERRORS: When you click Submit/Next and see errors, "
                f"read EACH error message carefully. They may be about DIFFERENT fields. "
                f"Do NOT assume all errors are about the last field you edited. "
                f"Fix each specific field mentioned in the error."
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
                        // --- PHASE 1: label-based search (best for radios/checkboxes) ---
                        const labels = [...document.querySelectorAll('label')];
                        const exactLabel = labels.find(l =>
                            l.textContent.trim() === t && l.offsetParent !== null
                        );
                        const partialLabel = !exactLabel && labels.find(l =>
                            l.textContent.trim().includes(t) && l.offsetParent !== null
                        );
                        const matchLabel = exactLabel || partialLabel;
                        if (matchLabel) {
                            const forId = matchLabel.getAttribute('for');
                            if (forId) {
                                const inp = document.getElementById(forId);
                                if (inp && (inp.type === 'radio' || inp.type === 'checkbox')) {
                                    const r = inp.getBoundingClientRect();
                                    const style = getComputedStyle(inp);
                                    const hidden = r.width < 5 || r.height < 5
                                        || style.opacity === '0' || style.visibility === 'hidden';
                                    el = hidden ? matchLabel : inp;
                                } else {
                                    el = matchLabel;
                                }
                            }
                            if (!el) el = matchLabel;
                        }

                        // --- PHASE 2: scoped selector search ---
                        if (!el) {
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
                                && e.children.length === 0
                            );
                            if (!el) {
                                const shorter = t.substring(0, 30);
                                el = scope.find(e =>
                                    e.textContent.includes(shorter)
                                    && e.offsetParent !== null
                                    && e.children.length === 0
                                );
                            }
                        }

                        // --- PHASE 3: broad fallback (leaf elements first) ---
                        if (!el) {
                            const allEls = [...document.querySelectorAll('*')];
                            const textEl = allEls.find(e =>
                                e.children.length === 0
                                && e.innerText && e.innerText.includes(t)
                                && e.offsetParent !== null
                            ) || allEls.find(e =>
                                e.innerText && e.innerText.includes(t)
                                && e.offsetParent !== null
                            );
                            if (textEl) {
                                const cb = textEl.closest('label, [role="checkbox"], [role="radio"]');
                                if (cb) el = cb;
                                else {
                                    const parent = textEl.parentElement;
                                    if (parent) {
                                        const nearCb = parent.querySelector(
                                            'input[type="checkbox"], input[type="radio"], [role="checkbox"]'
                                        );
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

                    // --- Resolve click target ---
                    let target = el;

                    // If we landed on a hidden radio/checkbox input, click its label instead
                    if (el.tagName === 'INPUT'
                        && (el.type === 'radio' || el.type === 'checkbox')) {
                        const r = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const hidden = r.width < 5 || r.height < 5
                            || style.opacity === '0' || style.visibility === 'hidden';
                        if (hidden && el.id) {
                            const lbl = document.querySelector('label[for="' + el.id + '"]');
                            if (lbl) target = lbl;
                        }
                    }

                    // Walk up to find ARIA radio/checkbox/option container
                    let radio = target.closest('[role="radio"], [role="checkbox"], [role="option"]');
                    if (!radio) {
                        for (let p = target.parentElement; p && p !== document.body; p = p.parentElement) {
                            const r = p.getAttribute('role');
                            if (r === 'radio' || r === 'checkbox' || r === 'option') {
                                radio = p; break;
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
                const SELECTORS = [
                    'input:not([type="hidden"])', 'textarea', 'select',
                    '[role="combobox"]', '[role="searchbox"]', '[role="spinbutton"]',
                    '[role="textbox"]', '[contenteditable="true"]',
                    '[data-automation-id] input',
                    '[class*="select__input"]', '[class*="dropdown__input"]',
                    '[class*="MuiInput"] input', '[class*="MuiOutlinedInput"] input',
                    '[class*="MuiAutocomplete"] input',
                    '[class*="ant-select"] input', '[class*="ant-input"]',
                    '[class*="choices__input"]',
                    '[class*="mat-input"]', '[class*="mat-select"]',
                    'mat-select', 'input[matinput]', 'input[matInput]',
                    '[class*="v-select"] input', '[class*="v-text-field"] input',
                    '[class*="v-autocomplete"] input',
                    '[class*="el-input"] input', '[class*="el-select"] input',
                    '[class*="el-autocomplete"] input',
                    '[class*="p-autocomplete"] input', '[class*="p-dropdown"]',
                    '[class*="p-inputtext"]',
                    '[class*="multiselect__input"]',
                    '[class*="vs__search"]',
                    '[class*="selectize-input"] input',
                    '[class*="select2-search"] input',
                    '[class*="chosen-search"] input',
                    '[class*="tom-select"] input'
                ].join(', ');
                let input;

                function findNearInput(el) {
                    if (!el) return null;
                    let found = el.querySelector(SELECTORS);
                    if (found) return found;
                    let sib = el.nextElementSibling;
                    for (let i = 0; i < 5 && sib; i++) {
                        found = sib.querySelector(SELECTORS)
                            || (sib.matches && sib.matches(SELECTORS) ? sib : null);
                        if (found) return found;
                        sib = sib.nextElementSibling;
                    }
                    let prev = el.previousElementSibling;
                    for (let i = 0; i < 3 && prev; i++) {
                        found = prev.querySelector(SELECTORS)
                            || (prev.matches && prev.matches(SELECTORS) ? prev : null);
                        if (found) return found;
                        prev = prev.previousElementSibling;
                    }
                    const parent = el.closest(
                        '.form-group, .form-field, .form-row, .form-item, ' +
                        '[class*="field"], [class*="Field"], [class*="form-control"], ' +
                        '[data-automation-id], [class*="container"], [class*="wrapper"], ' +
                        '[class*="MuiFormControl"], [class*="MuiTextField"], ' +
                        '[class*="ant-form-item"], [class*="ant-row"], ' +
                        '[class*="mat-form-field"], [class*="mat-input"], ' +
                        '[class*="v-input"], [class*="v-field"], ' +
                        '[class*="el-form-item"], [class*="el-input"], ' +
                        '[class*="p-field"], [class*="p-float-label"], ' +
                        '[class*="FormField"], [class*="input-group"], ' +
                        '[class*="choices"], [class*="multiselect"], ' +
                        '[class*="vs__dropdown"], [class*="selectize-control"], ' +
                        '[class*="select2-container"], [class*="chosen-container"], ' +
                        '[class*="tom-select"], fieldset'
                    );
                    if (parent) {
                        found = parent.querySelector(SELECTORS);
                        if (found) return found;
                    }
                    return null;
                }

                function textMatchScore(text) {
                    const t = text.trim().replace(/\\s*\\*\\s*$/, '').replace(/\\s+/g, ' ');
                    const l = label.replace(/\\s+/g, ' ');
                    const lc = labelClean.replace(/\\s+/g, ' ');
                    if (t === l || t === lc) return 100;
                    if (t.includes(l) || t.includes(lc)) return 50;
                    if (l.includes(t) || lc.includes(t)) return 10;
                    return 0;
                }

                if (label) {
                    const labels = [...document.querySelectorAll('label')];
                    let bestScore = 0;
                    let match = null;
                    for (const lbl of labels) {
                        const score = textMatchScore(lbl.textContent);
                        if (score > bestScore) { bestScore = score; match = lbl; }
                    }
                    if (match) {
                        const forId = match.getAttribute('for');
                        if (forId) input = document.getElementById(forId);
                        if (!input) input = match.querySelector(SELECTORS);
                        if (!input) input = findNearInput(match);
                    }
                    if (!input) {
                        const allText = [...document.querySelectorAll(
                            'span, div, p, h3, h4, h5, strong, legend, dt, ' +
                            '[class*="label"], [class*="Label"]'
                        )];
                        let bestTextScore = 0;
                        let textEl = null;
                        for (const el of allText) {
                            if (el.children.length >= 4) continue;
                            const score = textMatchScore(el.textContent);
                            if (score > bestTextScore) { bestTextScore = score; textEl = el; }
                        }
                        if (textEl) input = findNearInput(textEl);
                    }
                    if (!input) input = document.querySelector(
                        `[aria-label*="${labelClean}"]`
                    );
                    if (!input) {
                        const allLabelled = document.querySelectorAll('[aria-labelledby]');
                        for (const el of allLabelled) {
                            const lblId = el.getAttribute('aria-labelledby');
                            const lblEl = lblId && document.getElementById(lblId);
                            if (lblEl && textMatchScore(lblEl.textContent) > 0) {
                                input = el; break;
                            }
                        }
                    }
                    if (input && !input.matches(SELECTORS)) {
                        const inner = input.querySelector(SELECTORS);
                        if (inner) input = inner;
                    }
                    if (!input) input = document.querySelector(
                        `input[placeholder*="${labelClean}"], textarea[placeholder*="${labelClean}"], ` +
                        `[role="combobox"][placeholder*="${labelClean}"], ` +
                        `[role="searchbox"][placeholder*="${labelClean}"], ` +
                        `[data-placeholder*="${labelClean}"]`
                    );
                }
                if (!input && name) {
                    input = document.querySelector(
                        `input[name*="${name}"], textarea[name*="${name}"], ` +
                        `select[name*="${name}"], [role="combobox"][name*="${name}"]`
                    );
                    if (!input) input = document.querySelector(
                        `[id*="${name}"][role="combobox"], [id*="${name}"][role="searchbox"], ` +
                        `input[id*="${name}"], textarea[id*="${name}"], select[id*="${name}"]`
                    );
                    if (!input) input = document.querySelector(
                        `[data-testid*="${name}"] input, [data-automation-id*="${name}"] input`
                    );
                }
                if (!input) return JSON.stringify({found: false});

                const cls = (input.classList || '').toString().toLowerCase();
                const isCombobox =
                    input.getAttribute('role') === 'combobox'
                    || input.getAttribute('role') === 'searchbox'
                    || input.getAttribute('aria-autocomplete') === 'list'
                    || input.getAttribute('aria-autocomplete') === 'both'
                    || input.getAttribute('aria-expanded') !== null
                    || input.getAttribute('aria-haspopup') === 'listbox'
                    || input.getAttribute('aria-haspopup') === 'true'
                    || input.getAttribute('list') !== null
                    || input.tagName === 'MAT-SELECT'
                    || cls.includes('select__input')
                    || cls.includes('dropdown__input')
                    || cls.includes('autocomplete')
                    || cls.includes('typeahead')
                    || cls.includes('combobox')
                    || cls.includes('choices__input')
                    || cls.includes('react-select')
                    || cls.includes('ant-select')
                    || cls.includes('muiautocomplete')
                    || cls.includes('mat-select')
                    || cls.includes('mat-autocomplete')
                    || cls.includes('v-select')
                    || cls.includes('v-autocomplete')
                    || cls.includes('el-select')
                    || cls.includes('el-autocomplete')
                    || cls.includes('p-autocomplete')
                    || cls.includes('p-dropdown')
                    || cls.includes('multiselect__input')
                    || cls.includes('vs__search')
                    || cls.includes('selectize-input')
                    || cls.includes('select2-search')
                    || cls.includes('chosen-search')
                    || cls.includes('tom-select')
                    || (input.closest && (
                        input.closest('[class*="autocomplete"]')
                        || input.closest('[class*="Autocomplete"]')
                        || input.closest('[class*="react-select"]')
                        || input.closest('[class*="combobox"]')
                        || input.closest('[class*="typeahead"]')
                        || input.closest('[class*="dropdown__control"]')
                        || input.closest('[class*="select__control"]')
                        || input.closest('[class*="mat-form-field"]')
                        || input.closest('[class*="mat-autocomplete"]')
                        || input.closest('[class*="v-select"]')
                        || input.closest('[class*="v-autocomplete"]')
                        || input.closest('[class*="v-combobox"]')
                        || input.closest('[class*="el-select"]')
                        || input.closest('[class*="el-autocomplete"]')
                        || input.closest('[class*="p-autocomplete"]')
                        || input.closest('[class*="p-dropdown"]')
                        || input.closest('[class*="multiselect"]')
                        || input.closest('[class*="vs__dropdown"]')
                        || input.closest('[class*="selectize-control"]')
                        || input.closest('[class*="select2-container"]')
                        || input.closest('[class*="chosen-container"]')
                        || input.closest('[class*="tom-select"]')
                        || input.closest('[data-automation-id*="combobox"]')
                    ) !== null);
                const isSelect = input.tagName === 'SELECT';
                input.scrollIntoView({block: 'center'});
                input.focus();
                input.click();
                return JSON.stringify({
                    found: true, tag: input.tagName,
                    name: input.name || '', id: input.id || '',
                    type: input.type || '',
                    isCombobox: isCombobox,
                    isSelect: isSelect
                });
            }"""

            async def _cdp_clear_field(browser_session, page):
                """Select all and delete the current field content."""
                cdp_session = await browser_session.get_or_create_cdp_session()
                client = cdp_session.cdp_client
                sid = cdp_session.session_id
                await page.evaluate("() => document.activeElement && document.execCommand('selectAll')")
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

            async def _cdp_type(browser_session, page, text):
                """Clear field and insert text all at once via CDP (fast, for plain inputs)."""
                await _cdp_clear_field(browser_session, page)
                cdp_session = await browser_session.get_or_create_cdp_session()
                client = cdp_session.cdp_client
                sid = cdp_session.session_id
                await client.send_raw(
                    "Input.insertText", {"text": text}, session_id=sid,
                )
                await page.evaluate("""() => {
                    const el = document.activeElement;
                    if (!el) return;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""")

            async def _cdp_type_char_by_char(browser_session, page, text, delay_ms=40, clear=True):
                """Type text character-by-character via CDP key events.
                Mimics Playwright's type() method exactly: keyDown(text) → char → keyUp.
                The keyDown event with `text` param inserts the character (trusted event),
                the `char` event provides keypress notification for frameworks like React Select."""
                if clear:
                    await _cdp_clear_field(browser_session, page)
                cdp_session = await browser_session.get_or_create_cdp_session()
                client = cdp_session.cdp_client
                sid = cdp_session.session_id
                import asyncio as _aio
                for ch in text:
                    await client.send_raw(
                        "Input.dispatchKeyEvent",
                        {"type": "keyDown", "key": ch, "code": "",
                         "text": ch, "unmodifiedText": ch},
                        session_id=sid,
                    )
                    await client.send_raw(
                        "Input.dispatchKeyEvent",
                        {"type": "char", "key": ch, "code": "",
                         "text": ch, "unmodifiedText": ch},
                        session_id=sid,
                    )
                    await client.send_raw(
                        "Input.dispatchKeyEvent",
                        {"type": "keyUp", "key": ch, "code": ""},
                        session_id=sid,
                    )
                    if delay_ms > 0:
                        await _aio.sleep(delay_ms / 1000)

            @tools.action(description=(
                "Fill a text input field by its visible label text (e.g. 'Soyadı', 'E-posta'). "
                "Finds the field by label, clears it, and types the value via CDP (trusted events). "
                "Works with plain inputs, textareas, contenteditable, date/number inputs, "
                "React, Angular, Vue, Svelte, and Workday fields. "
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

                    if info.get("isSelect"):
                        select_js = """(args) => {
                            const el = document.activeElement;
                            if (!el || el.tagName !== 'SELECT') return 'NOT_SELECT';
                            const val = args.value.toLowerCase();
                            for (const opt of el.options) {
                                if (opt.text.toLowerCase().includes(val) ||
                                    opt.value.toLowerCase().includes(val)) {
                                    el.value = opt.value;
                                    el.dispatchEvent(new Event('change', {bubbles: true}));
                                    return 'Selected: ' + opt.text;
                                }
                            }
                            el.value = args.value;
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return 'Set value: ' + args.value;
                        }"""
                        result = await page.evaluate(select_js, {"value": value})
                        msg = f"fill_text_field(select): label='{label}' → {result}"
                        logger.info(msg)
                        return ActionResult(extracted_content=msg)

                    tag = info.get("tag", "").upper()
                    input_type = info.get("type", "").lower()

                    if tag == "DIV" or info.get("tag", "") in ("DIV", "SPAN", "P"):
                        await page.evaluate("""(args) => {
                            const el = document.activeElement;
                            if (el) {
                                el.textContent = args.value;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                        }""", {"value": value})
                    elif input_type in ("date", "datetime-local", "month", "week", "time"):
                        await page.evaluate("""(args) => {
                            const el = document.activeElement;
                            if (!el) return;
                            const proto = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value'
                            );
                            if (proto && proto.set) proto.set.call(el, args.value);
                            else el.value = args.value;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""", {"value": value})
                    else:
                        await _cdp_type(browser_session, page, value)

                    actual = await page.evaluate("""() => {
                        const el = document.activeElement;
                        if (!el) return '';
                        const val = el.value !== undefined ? el.value : (el.textContent || '');
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                        el.dispatchEvent(new FocusEvent('focusout', {bubbles: true}));
                        return val;
                    }""")
                    msg = (
                        f"Filled {info.get('tag','')} (label='{label}', name='{info.get('name','')}') "
                        f"with '{value}' (actual='{str(actual)[:80]}')"
                    )
                    logger.info("fill_text_field: %s", msg)
                    return ActionResult(extracted_content=msg)
                except Exception as e:
                    msg = f"fill_text_field error: {e}"
                    logger.warning(msg)
                    return ActionResult(extracted_content=msg)

            _CLICK_OPTION_JS = """(args) => {
                const val = args.value.toLowerCase().trim();
                const OPTION_SELECTORS = [
                    '[role="option"]',
                    '[role="listbox"] li',
                    'ul[role="listbox"] > li',
                    '[role="listbox"] > div',
                    '[role="presentation"] li',
                    '[role="menu"] li',
                    '[role="menu"] [role="menuitem"]',
                    'div[data-automation-id*="promptOption"]',
                    '[data-automation-id*="selectOption"]',
                    '[data-automation-id*="option"]',
                    '.css-1dimb5e-option',
                    '[class*="dropdown__option"]',
                    '[class*="select__option"]',
                    '[class*="option"]:not([class*="optionList"])',
                    '[class*="Option"]:not([class*="OptionList"])',
                    '[class*="suggestion"]',
                    '[class*="Suggestion"]',
                    '[class*="dropdown-item"]',
                    '[class*="dropdown__item"]',
                    '[class*="DropdownItem"]',
                    '[class*="menu-item"]',
                    '[class*="MenuItem"]',
                    '[class*="list-item"]',
                    '[class*="ListItem"]',
                    '[class*="autocomplete"] li',
                    '[class*="Autocomplete"] li',
                    '[class*="typeahead"] li',
                    '[class*="Typeahead"] li',
                    '[class*="choices__item--selectable"]',
                    '[class*="ant-select-item"]',
                    '[class*="ant-select-item-option"]',
                    '[class*="MuiAutocomplete-option"]',
                    'mat-option', '[class*="mat-option"]',
                    '[class*="mat-autocomplete"] [class*="mat-option"]',
                    '[class*="cdk-option"]',
                    '[class*="v-list-item"]',
                    '[class*="v-list-item__title"]',
                    '[class*="menuable__content"] [class*="v-list-item"]',
                    '.el-select-dropdown__item',
                    '[class*="el-autocomplete-suggestion"] li',
                    '[class*="el-select-dropdown"] li',
                    '[class*="p-autocomplete-item"]',
                    '[class*="p-autocomplete-panel"] li',
                    '[class*="p-dropdown-item"]',
                    '[class*="p-dropdown-items"] li',
                    '[class*="p-listbox-item"]',
                    '[class*="multiselect__element"]',
                    '[class*="multiselect__option"]',
                    '[class*="vs__dropdown-option"]',
                    '[class*="selectize-dropdown"] [class*="option"]',
                    '.select2-results__option',
                    '[class*="select2-results"] li',
                    '.chosen-results li',
                    '.ts-dropdown [class*="option"]',
                    '[class*="tom-select"] [class*="option"]',
                    '[class*="headlessui"] [role="option"]',
                    '[class*="Listbox"] [role="option"]',
                    '[class*="downshift"] li',
                    'datalist option',
                    'ul.ui-autocomplete li',
                    '.tt-suggestion',
                    '.pac-item',
                    '.awesomplete li',
                    '[class*="algolia"] [class*="hit"]',
                    '[class*="aa-Item"]'
                ].join(', ');

                const options = document.querySelectorAll(OPTION_SELECTORS);

                let exact = null;
                let startsWith = null;
                let includes = null;

                for (const opt of options) {
                    if (opt.offsetParent === null && !opt.closest('[role="listbox"]')) continue;
                    const txt = (opt.textContent || '').toLowerCase().trim();
                    if (!txt) continue;
                    if (txt === val) { exact = opt; break; }
                    if (!startsWith && txt.startsWith(val)) startsWith = opt;
                    if (!includes && txt.includes(val)) includes = opt;
                }

                const pick = exact || startsWith || includes;
                if (pick) {
                    pick.scrollIntoView({block: 'center'});
                    pick.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    pick.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    pick.click();
                    return 'Selected: ' + pick.textContent.trim().substring(0, 80);
                }

                const dropdownContainers = document.querySelectorAll(
                    '[role="listbox"], [class*="dropdown__menu"], [class*="select__menu"], ' +
                    '[class*="dropdown-menu"], [class*="autocomplete-panel"], ' +
                    '[class*="mat-autocomplete-panel"], [class*="cdk-overlay"], ' +
                    '[class*="v-menu__content"], [class*="el-select-dropdown"], ' +
                    '[class*="el-autocomplete-suggestion"], ' +
                    '[class*="p-autocomplete-panel"], [class*="p-dropdown-panel"], ' +
                    '[class*="vs__dropdown-menu"], [class*="multiselect__content"], ' +
                    '[class*="selectize-dropdown"], [class*="select2-results"], ' +
                    '[class*="chosen-results"], [class*="ts-dropdown"], ' +
                    '.ui-autocomplete, .tt-menu, .pac-container, ' +
                    '[class*="headlessui"][role="listbox"]'
                );
                for (const container of dropdownContainers) {
                    if (container.offsetParent === null && !container.closest('[role="listbox"]')) continue;
                    const items = container.querySelectorAll('div, li, span, [role="option"]');
                    for (const fb of items) {
                        const txt = (fb.textContent || '').toLowerCase().trim();
                        if (txt && txt.includes(val) && fb.children.length < 5) {
                            fb.scrollIntoView({block: 'center'});
                            fb.click();
                            return 'Selected (fallback): ' + fb.textContent.trim().substring(0, 80);
                        }
                    }
                }
                return 'NO_SUGGESTIONS';
            }"""

            @tools.action(description=(
                "Select a value from a native HTML <select> dropdown by label. "
                "Only works for native <select> elements — NOT for React Select, "
                "custom dropdowns, or autocomplete fields. "
                "For custom dropdowns (React Select, etc.), use the 'input' action to type "
                "and then 'click' the matching option from the screenshot."
            ))
            async def native_select(
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
                        return ActionResult(
                            extracted_content=f"native_select: field not found: label='{label}', name='{name}'"
                        )
                    if not info.get("isSelect"):
                        return ActionResult(
                            extracted_content=f"native_select: field is not a <select> element. "
                            f"Use 'input' action to type into the field, then 'click' the option."
                        )
                    select_js = """(args) => {
                        const el = document.activeElement;
                        if (!el || el.tagName !== 'SELECT') return 'NOT_SELECT';
                        const val = args.value.toLowerCase();
                        for (const opt of el.options) {
                            if (opt.text.toLowerCase().includes(val) ||
                                opt.value.toLowerCase().includes(val)) {
                                el.value = opt.value;
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                return 'Selected: ' + opt.text;
                            }
                        }
                        return 'NO_MATCH';
                    }"""
                    result = await page.evaluate(select_js, {"value": value})
                    msg = f"native_select(label='{label}', value='{value}'): {result}"
                    logger.info(msg)
                    return ActionResult(extracted_content=msg)
                except Exception as e:
                    msg = f"native_select error: {e}"
                    logger.warning(msg)
                    return ActionResult(extracted_content=msg)

            @tools.action(description=(
                "Set a form field value using JavaScript. Works for hidden inputs, "
                "native <select> elements, date/time/range/color inputs, contenteditable divs, "
                "custom dropdowns, checkboxes, radio buttons, and any element with a value property. "
                "Provide a CSS selector and the value to set. "
                "For checkboxes/radios, pass 'true'/'false' to check/uncheck. "
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
                    const tag = el.tagName.toUpperCase();
                    const type = (el.type || '').toLowerCase();

                    if (type === 'checkbox' || type === 'radio') {
                        const want = args.value === 'true' || args.value === '1';
                        if (el.checked !== want) {
                            el.checked = want;
                            el.click();
                        }
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return 'Toggled ' + type + ' to ' + el.checked;
                    }

                    if (el.getAttribute('contenteditable') === 'true') {
                        el.focus();
                        el.textContent = args.value;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return 'Set contenteditable to: ' + args.value;
                    }

                    if (tag === 'SELECT') {
                        const val = args.value.toLowerCase();
                        let matched = false;
                        for (const opt of el.options) {
                            if (opt.value === args.value || opt.text === args.value
                                || opt.text.toLowerCase().includes(val)
                                || opt.value.toLowerCase().includes(val)) {
                                el.value = opt.value;
                                matched = true;
                                break;
                            }
                        }
                        if (!matched) el.value = args.value;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return 'Selected: ' + el.options[el.selectedIndex]?.text || args.value;
                    }

                    const protos = {
                        'TEXTAREA': window.HTMLTextAreaElement.prototype,
                        'INPUT': window.HTMLInputElement.prototype,
                    };
                    const proto = protos[tag] || window.HTMLInputElement.prototype;
                    const nativeSet = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (nativeSet && nativeSet.set) {
                        nativeSet.set.call(el, args.value);
                    } else {
                        el.value = args.value;
                    }
                    if (el.setAttribute) el.setAttribute('value', args.value);

                    el.dispatchEvent(new Event('focus', {bubbles: true}));
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                    el.dispatchEvent(new FocusEvent('focusout', {bubbles: true}));
                    return 'Set ' + tag + '[type=' + type + '] to: ' + args.value;
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
                # tools=tools,  # DISABLED: testing with browser-use built-in actions only
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
