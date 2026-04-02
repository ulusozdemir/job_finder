from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    db_url: str = "sqlite:///jobs.db"

    # Gemini model to use (free tier)
    gemini_model: str = "gemini-2.5-flash"

    # Only notify if match score >= this value
    score_threshold: int = 60

    # Scraping settings
    scrape_delay_min: float = 2.0
    scrape_delay_max: float = 5.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
