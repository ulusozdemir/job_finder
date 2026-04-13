"""Fetch LinkedIn email verification codes via IMAP."""

from __future__ import annotations

import email
import imaplib
import logging
import re
import time

from config import settings

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"\b(\d{6})\b")


def _get_imap_credentials() -> tuple[str, str, str]:
    imap_email = settings.imap_email or settings.linkedin_email
    imap_password = settings.imap_password
    imap_server = settings.imap_server
    return imap_server, imap_email, imap_password


def fetch_linkedin_verification_code(
    max_wait: int = 60,
    poll_interval: int = 10,
    max_age_seconds: int = 120,
) -> str | None:
    """Poll IMAP inbox for a recent LinkedIn verification code.

    Returns the 6-digit code string, or None if not found within max_wait.
    """
    server, imap_email, imap_password = _get_imap_credentials()
    if not imap_email or not imap_password:
        logger.warning("IMAP credentials not configured, cannot fetch verification code")
        return None

    deadline = time.time() + max_wait

    while time.time() < deadline:
        try:
            code = _check_inbox(server, imap_email, imap_password, max_age_seconds)
            if code:
                logger.info("LinkedIn verification code found: %s", code)
                return code
        except Exception as e:
            logger.error("IMAP error: %s", e)

        remaining = deadline - time.time()
        if remaining > poll_interval:
            logger.info("No code yet, retrying in %ds...", poll_interval)
            time.sleep(poll_interval)
        else:
            break

    logger.warning("No LinkedIn verification code found within %ds", max_wait)
    return None


def _check_inbox(
    server: str, imap_email: str, imap_password: str, max_age_seconds: int
) -> str | None:
    """Connect to IMAP, search for recent LinkedIn emails, extract 6-digit code."""
    mail = imaplib.IMAP4_SSL(server)
    try:
        mail.login(imap_email, imap_password)
        mail.select("INBOX")

        _, msg_ids = mail.search(None, '(FROM "linkedin" UNSEEN)')
        if not msg_ids or not msg_ids[0]:
            return None

        ids = msg_ids[0].split()
        # Check newest emails first
        for msg_id in reversed(ids):
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            msg_date = email.utils.parsedate_to_datetime(msg.get("Date", ""))
            age = time.time() - msg_date.timestamp()
            if age > max_age_seconds:
                continue

            body = _extract_body(msg)
            match = _CODE_RE.search(body)
            if match:
                mail.store(msg_id, "+FLAGS", "\\Seen")
                return match.group(1)

        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
            elif content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode("utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""
