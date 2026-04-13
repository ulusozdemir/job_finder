"""Poll Telegram for 'apply' callback queries triggered by the Basvur button."""

from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


def get_pending_applications() -> list[dict]:
    """Fetch callback queries and return list of {job_id, callback_query_id}."""
    if not settings.telegram_bot_token:
        return []

    base = TELEGRAM_API.format(token=settings.telegram_bot_token)
    results: list[dict] = []

    try:
        resp = httpx.get(
            f"{base}/getUpdates",
            params={"allowed_updates": '["callback_query"]', "timeout": 5},
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("getUpdates failed: %s", data)
            return []

        updates = data.get("result", [])
        max_update_id = 0

        for update in updates:
            uid = update.get("update_id", 0)
            if uid > max_update_id:
                max_update_id = uid

            cb = update.get("callback_query")
            if not cb:
                continue

            cb_data = cb.get("data", "")
            if not cb_data.startswith("apply:"):
                continue

            job_id = cb_data.split(":", 1)[1]
            results.append({
                "job_id": job_id,
                "callback_query_id": cb["id"],
            })

        if max_update_id:
            httpx.get(
                f"{base}/getUpdates",
                params={"offset": max_update_id + 1},
                timeout=10,
            )

    except Exception as e:
        logger.error("Error polling Telegram: %s", e)

    return results


def answer_callback(callback_query_id: str, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    base = TELEGRAM_API.format(token=settings.telegram_bot_token)
    try:
        httpx.post(
            f"{base}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.error("Error answering callback: %s", e)
