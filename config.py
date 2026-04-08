from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    db_url: str = "sqlite:///jobs.db"

    # Gemma 4 26B via Gemini API (free tier)
    gemini_model: str = "gemma-4-26b-a4b-it"

    # Gemma 4 free-tier rate limits
    gemini_rpm: int = 15      # requests per minute
    gemini_rpd: int = 1500    # requests per day
    pipeline_runs_per_day: int = 4  # how many times pipeline runs daily
    gemini_max_per_run: int = 375   # rpd / runs_per_day

    # Only notify if match score >= this value
    score_threshold: int = 60

    # Content dedup: ignore duplicates only within this window (days).
    # If the same title+company was notified more than N days ago, treat as new.
    dedup_days: int = 7

    # Scraping settings — keep close to per-run scoring budget
    min_filtered_jobs: int = 50
    scrape_delay_min: float = 2.0
    scrape_delay_max: float = 5.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
