"""
Environment-driven configuration for the ingestion service.
Never hard-code secrets here — everything comes from the environment,
loaded from .env locally and from Render's environment settings in prod.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_service_role_key: str  # server-side only; bypasses RLS by design

    # Auth for admin-triggered / cron-triggered sync endpoints
    ingestion_secret: str
    cron_secret: str

    # Networking / politeness defaults (can be overridden per-source in DB)
    default_user_agent: str = (
        "NepalNewsMagazineBot/1.0 (+https://example.com/bot; contact=admin@example.com)"
    )
    default_request_timeout_seconds: float = 10.0
    max_concurrent_fetches: int = 3

    # Scheduler
    scheduler_enabled: bool = True

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
