"""Parse NET monthly TL from profile text; NET/GROSS equivalents using live FX + rough net→gross."""

from __future__ import annotations

import re
from dataclasses import dataclass


def parse_tl_net_monthly(s: str) -> float | None:
    """Extract a monthly NET amount in TRY from strings like '190000 TL net/month'."""
    if not s or not s.strip():
        return None
    m = re.search(r"([\d.,]+)\s*(?:TL|TRY|tl|try)\b", s)
    if not m:
        return None
    raw = m.group(1).strip()
    if "." in raw and "," not in raw:
        parts = raw.split(".")
        if len(parts) > 1 and all(p.isdigit() for p in parts) and len(parts[-1]) == 3:
            return float("".join(parts))
    raw = raw.replace(",", "").replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class SalaryEquivalents:
    """try_per_usd / try_per_eur = TRY per 1 USD / 1 EUR. Gross figures use net_to_gross multiplier (approximate)."""

    tl_monthly_net: float
    usd_monthly_net: int
    usd_annual_net: int
    eur_monthly_net: int
    eur_annual_net: int
    tl_monthly_gross_approx: int
    usd_monthly_gross_approx: int
    usd_annual_gross_approx: int
    eur_monthly_gross_approx: int
    eur_annual_gross_approx: int
    net_to_gross: float
    try_per_usd: float
    try_per_eur: float
    rate_source: str


def compute_equivalents(
    salary_expectation: str,
    try_per_usd: float,
    try_per_eur: float,
    rate_source: str = "",
    net_to_gross: float = 1.47,
) -> SalaryEquivalents | None:
    tl = parse_tl_net_monthly(salary_expectation)
    if tl is None or try_per_usd <= 0 or try_per_eur <= 0 or net_to_gross <= 0:
        return None
    usd_m = int(round(tl / try_per_usd))
    eur_m = int(round(tl / try_per_eur))
    tg = int(round(tl * net_to_gross))
    usd_g = int(round(tg / try_per_usd))
    eur_g = int(round(tg / try_per_eur))
    return SalaryEquivalents(
        tl_monthly_net=tl,
        usd_monthly_net=usd_m,
        usd_annual_net=usd_m * 12,
        eur_monthly_net=eur_m,
        eur_annual_net=eur_m * 12,
        tl_monthly_gross_approx=tg,
        usd_monthly_gross_approx=usd_g,
        usd_annual_gross_approx=usd_g * 12,
        eur_monthly_gross_approx=eur_g,
        eur_annual_gross_approx=eur_g * 12,
        net_to_gross=net_to_gross,
        try_per_usd=try_per_usd,
        try_per_eur=try_per_eur,
        rate_source=rate_source or "unknown",
    )


def format_equivalents_hint(eq: SalaryEquivalents) -> str:
    return (
        f"LIVE NET (rates: {eq.rate_source}; 1 USD = {eq.try_per_usd:.4f} TRY, "
        f"1 EUR = {eq.try_per_eur:.4f} TRY): {eq.tl_monthly_net:.0f} TL/month ~ "
        f"${eq.usd_monthly_net}/mo (~${eq.usd_annual_net}/yr); "
        f"~{eq.eur_monthly_net} EUR/mo (~{eq.eur_annual_net} EUR/yr).\n"
        f"APPROX GROSS (net x {eq.net_to_gross:g}, Turkey ballpark; exact brüt depends on tax bracket): "
        f"{eq.tl_monthly_gross_approx} TL/mo ~ ${eq.usd_monthly_gross_approx}/mo (~${eq.usd_annual_gross_approx}/yr); "
        f"~{eq.eur_monthly_gross_approx} EUR/mo (~{eq.eur_annual_gross_approx} EUR/yr)."
    )
