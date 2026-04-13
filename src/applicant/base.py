from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config import settings

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("screenshots")


@dataclass
class ApplicantProfile:
    first_name: str
    last_name: str
    email: str
    phone: str
    linkedin_url: str
    location: str
    address_line: str
    city: str
    postal_code: str
    district: str
    education: str
    university: str
    current_company: str
    summary: str
    salary_expectation: str = ""
    english_proficiency: str = ""
    nationality: str = ""
    gender: str = ""
    date_of_birth: str = ""
    military_status: str = ""
    work_authorization: str = ""
    notice_period: str = ""
    willing_to_relocate: str = ""
    work_mode_preference: str = ""
    hear_about_us: str = ""
    skills: list[str] = field(default_factory=list)
    experience_years: int = 0
    cv_path: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass
class ApplyResult:
    success: bool
    message: str = ""
    adapter_used: str = ""


class BaseAdapter:
    """Abstract base for all ATS adapters."""

    name: str = "base"

    async def apply(self, url: str, profile: ApplicantProfile) -> ApplyResult:
        raise NotImplementedError


async def take_screenshot(page, adapter_name: str, step: str) -> str | None:
    """Save a timestamped screenshot and return the file path."""
    try:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"{adapter_name}_{step}_{ts}.png"
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Screenshot saved: %s", path)
        return str(path)
    except Exception as e:
        logger.warning("Screenshot failed: %s", e)
        return None


def load_applicant_profile() -> ApplicantProfile:
    profile_path = Path("profile.yaml")
    with open(profile_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    personal = data.get("personal", {})
    return ApplicantProfile(
        first_name=personal.get("first_name", ""),
        last_name=personal.get("last_name", ""),
        email=settings.applicant_email,
        phone=settings.applicant_phone,
        linkedin_url=personal.get("linkedin_url", ""),
        location=personal.get("location", ""),
        address_line=personal.get("address_line", ""),
        city=personal.get("city", ""),
        postal_code=personal.get("postal_code", ""),
        district=personal.get("district", ""),
        education=personal.get("education", ""),
        university=personal.get("university", ""),
        current_company=personal.get("current_company", ""),
        summary=data.get("summary", ""),
        salary_expectation=data.get("salary_expectation", ""),
        english_proficiency=data.get("english_proficiency", ""),
        nationality=personal.get("nationality", ""),
        gender=personal.get("gender", ""),
        date_of_birth=personal.get("date_of_birth", ""),
        military_status=personal.get("military_status", ""),
        work_authorization=personal.get("work_authorization", ""),
        notice_period=personal.get("notice_period", ""),
        willing_to_relocate=personal.get("willing_to_relocate", ""),
        work_mode_preference=personal.get("work_mode_preference", ""),
        hear_about_us=personal.get("hear_about_us", ""),
        skills=data.get("skills", []),
        experience_years=data.get("experience_years", 0),
        cv_path=settings.cv_path,
    )


# ── Rule-based field matching utilities ──────────────────────

_FIELD_PATTERNS: dict[str, list[str]] = {
    "first_name": [r"first.?name", r"given.?name", r"ad[ıi]n[ıi]z"],
    "last_name": [r"last.?name", r"sur.?name", r"family.?name", r"soyad"],
    "full_name": [r"full.?name", r"^name$", r"your.?name", r"ad.?soyad"],
    "email": [r"e?.?mail", r"e-posta"],
    "phone": [r"phone", r"mobile", r"telefon", r"tel\.?$"],
    "linkedin": [r"linkedin", r"profile.?url"],
    "location": [r"location", r"city", r"address", r"konum", r"sehir"],
    "education": [r"education", r"degree", r"egitim"],
    "university": [r"university", r"school", r"college", r"okul"],
    "current_company": [r"current.?company", r"company", r"employer", r"sirket", r"firma"],
    "experience_years": [r"years?.?of?.?experience", r"deneyim.?y[ıi]l"],
    "salary_expectation": [r"salary", r"compensation", r"expected.?salary", r"maa[sş]", r"[üu]cret"],
    "english_proficiency": [r"english", r"language.?proficiency", r"ingilizce", r"dil.?seviye"],
    "nationality": [r"nationality", r"citizenship", r"uyruk", r"vatanda[sş]"],
    "gender": [r"gender", r"sex", r"cinsiyet"],
    "date_of_birth": [r"date.?of.?birth", r"birth.?date", r"dob", r"do[gğ]um.?tarih"],
    "military_status": [r"military", r"askerlik"],
    "work_authorization": [r"authorized?.?to.?work", r"work.?permit", r"visa", r"sponsor", r"[cç]al[ıi][sş]ma.?izn"],
    "notice_period": [r"notice.?period", r"when.?can.?you.?start", r"availability", r"start.?date", r"ihbar.?s[üu]re"],
    "willing_to_relocate": [r"reloca", r"ta[sş][ıi]n"],
    "work_mode_preference": [r"work.?mode", r"remote.?hybrid", r"onsite.?remote", r"[cç]al[ıi][sş]ma.?model"],
    "hear_about_us": [r"hear.?about", r"how.?did.?you.?find", r"referral.?source", r"nas[ıi]l.?duydun"],
}


def match_field(label: str) -> str | None:
    """Return the profile field name that best matches a form label, or None."""
    normalised = label.strip().lower()
    for field_key, patterns in _FIELD_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, normalised, re.IGNORECASE):
                return field_key
    return None


def get_field_value(field_key: str, profile: ApplicantProfile) -> str:
    """Return the string value for a matched field key."""
    mapping = {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin": profile.linkedin_url,
        "location": profile.location,
        "education": profile.education,
        "university": profile.university,
        "current_company": profile.current_company,
        "experience_years": str(profile.experience_years),
        "salary_expectation": profile.salary_expectation,
        "english_proficiency": profile.english_proficiency,
        "nationality": profile.nationality,
        "gender": profile.gender,
        "date_of_birth": profile.date_of_birth,
        "military_status": profile.military_status,
        "work_authorization": profile.work_authorization,
        "notice_period": profile.notice_period,
        "willing_to_relocate": profile.willing_to_relocate,
        "work_mode_preference": profile.work_mode_preference,
        "hear_about_us": profile.hear_about_us,
    }
    return mapping.get(field_key, "")
