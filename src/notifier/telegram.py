import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_MD2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _escape_md(text: str) -> str:
    """Escape ALL Telegram MarkdownV2 special chars."""
    for ch in _MD2_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_message(
    title: str,
    company: str,
    location: str,
    url: str,
    score: int,
    reasons: list[str],
    missing_skills: list[str],
) -> str:
    reasons_text = "\n".join(f"  \\- {_escape_md(r)}" for r in reasons)
    missing_text = ", ".join(_escape_md(s) for s in missing_skills) if missing_skills else "None"
    esc_title = _escape_md(title)
    esc_company = _escape_md(company)
    esc_location = _escape_md(location)
    esc_score = _escape_md(str(score))

    return (
        f"🎯 *Match Score: {esc_score}/100*\n\n"
        f"*{esc_title}*\n"
        f"🏢 {esc_company}\n"
        f"📍 {esc_location}\n\n"
        f"*Why it matches:*\n{reasons_text}\n\n"
        f"*Missing skills:* {missing_text}\n\n"
        f"[View Job]({_escape_md(url)})"
    )


def send_alert(message: str) -> bool:
    """Send a plain-text alert (rate limits, errors, etc.)."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False

    api_url = TELEGRAM_API.format(token=settings.telegram_bot_token)
    try:
        resp = httpx.post(
            api_url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return True
        logger.error("Telegram alert error %d: %s", resp.status_code, resp.text)
        return False
    except Exception:
        return False


def send_job_notification(
    title: str,
    company: str,
    location: str,
    url: str,
    score: int,
    reasons: list[str],
    missing_skills: list[str],
) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram credentials not configured, skipping notification")
        return False

    message = _format_message(title, company, location, url, score, reasons, missing_skills)
    api_url = TELEGRAM_API.format(token=settings.telegram_bot_token)

    try:
        resp = httpx.post(
            api_url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": message,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("Telegram notification sent for: %s", title)
            return True
        else:
            logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False
