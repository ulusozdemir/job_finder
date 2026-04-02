from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    db_url: str = "sqlite:///jobs.db"

    # Gemini model to use (free tier)
    gemini_model: str = "gemini-2.5-flash"

    # Gemini free-tier rate limits
    gemini_rpm: int = 5       # requests per minute
    gemini_rpd: int = 20      # requests per day
    pipeline_runs_per_day: int = 4  # how many times pipeline runs daily
    gemini_max_per_run: int = 5     # auto-calculated: rpd / runs_per_day

    # Only notify if match score >= this value
    score_threshold: int = 60

    # Scraping settings — keep close to per-run scoring budget
    min_filtered_jobs: int = 5
    scrape_delay_min: float = 2.0
    scrape_delay_max: float = 5.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
