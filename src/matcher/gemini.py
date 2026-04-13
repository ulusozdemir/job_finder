import json
import logging

from google import genai
from google.genai.errors import ClientError

from config import settings
from src.matcher.profile import Profile
from src.notifier.telegram import send_alert

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


SCORE_PROMPT = """\
You are a job-matching assistant. Compare the candidate profile with the job posting \
and return a JSON object with exactly these keys:
- "score": integer 0-100 (how well the job matches the candidate)
- "reasons": list of 2-4 short strings explaining the score
- "missing_skills": list of skills the job requires that the candidate lacks
- "rejection_reason": a single short string explaining why the score is not higher (what holds it back)

Be strict: a generic "software engineer" job with no skill overlap should score below 30.
A perfect match (same stack, same seniority, remote if preferred) should score 85-100.

## Candidate Profile
{profile}

## Job Posting
Title: {title}
Company: {company}
Location: {location}
Description:
{description}
"""

_FALLBACK_SCORE = {"score": 0, "reasons": ["Failed to parse AI response"], "missing_skills": [], "rejection_reason": "AI response parsing failed"}


def _build_profile_text(profile: Profile) -> str:
    skills = ", ".join(profile.skills)
    roles = ", ".join(profile.preferred_roles)
    return (
        f"{profile.summary.strip()}\n"
        f"Skills: {skills}\n"
        f"Experience: {profile.experience_years} years\n"
        f"Looking for: {roles}"
    )


def _parse_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    result = json.loads(text)
    if isinstance(result, list):
        result = result[0] if result else {}
    return {
        "score": int(result.get("score", 0)),
        "reasons": result.get("reasons", []),
        "missing_skills": result.get("missing_skills", []),
        "rejection_reason": result.get("rejection_reason", ""),
    }


def score_job(
    profile: Profile,
    title: str,
    company: str,
    location: str,
    description: str,
) -> dict:
    """Score a single job against the profile. Returns parsed JSON."""
    client = _get_client()

    prompt = SCORE_PROMPT.format(
        profile=_build_profile_text(profile),
        title=title,
        company=company,
        location=location,
        description=description[:6000],
    )

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return _parse_response(response.text)
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse AI response: %s", e)
        return {**_FALLBACK_SCORE}
    except ClientError as e:
        logger.error("AI API client error: %s", e)
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            send_alert(
                "⚠️ AI API rate limit hit!\n\n"
                f"Error: {e}\n\n"
                "Scoring is paused for this run. "
                "Remaining jobs will be scored in the next run."
            )
            raise
        return {"score": 0, "reasons": [f"API error: {e}"], "missing_skills": [], "rejection_reason": str(e)[:200]}
    except Exception as e:
        logger.error("AI API error: %s", e)
        send_alert(f"⚠️ AI API unexpected error:\n\n{e}")
        return {"score": 0, "reasons": [f"API error: {e}"], "missing_skills": [], "rejection_reason": str(e)[:200]}
