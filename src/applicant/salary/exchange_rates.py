"""Fetch live TRY/USD and TRY/EUR rates (TRY per 1 USD / 1 EUR) for salary hints."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import NamedTuple

import httpx

logger = logging.getLogger(__name__)

TCMB_TODAY_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
OPEN_ER_USD = "https://open.er-api.com/v6/latest/USD"
OPEN_ER_EUR = "https://open.er-api.com/v6/latest/EUR"


class LiveRates(NamedTuple):
    """How many TRY for one unit of foreign currency (for division: TL_amount / rate = FX)."""

    try_per_usd: float
    try_per_eur: float
    source: str


def _fetch_tcmb() -> LiveRates | None:
    try:
        resp = httpx.get(TCMB_TODAY_URL, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        try_per_usd: float | None = None
        try_per_eur: float | None = None
        for cur in root.findall(".//Currency"):
            code = cur.get("CurrencyCode")
            if code not in ("USD", "EUR"):
                continue
            el = cur.find("ForexSelling")
            if el is None or not (el.text and el.text.strip()):
                continue
            val = float(el.text.strip().replace(",", "."))
            if code == "USD":
                try_per_usd = val
            else:
                try_per_eur = val
        if try_per_usd and try_per_eur and try_per_usd > 0 and try_per_eur > 0:
            logger.info(
                "Exchange rates from TCMB: 1 USD = %.4f TRY, 1 EUR = %.4f TRY",
                try_per_usd,
                try_per_eur,
            )
            return LiveRates(try_per_usd, try_per_eur, "TCMB")
    except Exception as e:
        logger.warning("TCMB exchange rate fetch failed: %s", e)
    return None


def _fetch_open_er() -> LiveRates | None:
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            u = client.get(OPEN_ER_USD)
            u.raise_for_status()
            ju = u.json()
            if ju.get("result") != "success":
                return None
            try_per_usd = ju.get("rates", {}).get("TRY")
            e = client.get(OPEN_ER_EUR)
            e.raise_for_status()
            je = e.json()
            if je.get("result") != "success":
                return None
            try_per_eur = je.get("rates", {}).get("TRY")
        if try_per_usd and try_per_eur:
            tu, te = float(try_per_usd), float(try_per_eur)
            if tu > 0 and te > 0:
                logger.info(
                    "Exchange rates from open.er-api: 1 USD = %.4f TRY, 1 EUR = %.4f TRY",
                    tu,
                    te,
                )
                return LiveRates(tu, te, "open.er-api")
    except Exception as e:
        logger.warning("open.er-api exchange rate fetch failed: %s", e)
    return None


def fetch_live_try_rates() -> LiveRates | None:
    """Return live TRY per USD and TRY per EUR, or None if all sources fail."""
    rates = _fetch_tcmb()
    if rates:
        return rates
    return _fetch_open_er()
