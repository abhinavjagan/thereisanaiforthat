from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/aitools"
    database_url_sync: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/aitools"

    # API keys
    github_token: str = ""
    producthunt_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "aitools-aggregator/1.0"
    openai_api_key: str = ""

    # App
    log_level: str = "INFO"
    llm_enrichment_enabled: bool = False
    llm_max_per_day: int = 100


settings = Settings()
