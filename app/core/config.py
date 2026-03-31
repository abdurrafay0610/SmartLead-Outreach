from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/outreach"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/outreach"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Smartlead
    SMARTLEAD_API_KEY: str = ""
    SMARTLEAD_BASE_URL: str = "https://server.smartlead.ai/api/v1"

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # Smartlead rate limits (default to Standard tier)
    SMARTLEAD_REQUESTS_PER_MINUTE: int = 60
    SMARTLEAD_REQUESTS_PER_HOUR: int = 1000
    SMARTLEAD_BURST_PER_SECOND: int = 10

    # Lead batch size (Smartlead max is 400)
    LEAD_BATCH_SIZE: int = 400


@lru_cache
def get_settings() -> Settings:
    return Settings()
