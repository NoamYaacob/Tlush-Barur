"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App identity
    app_name: str = "תלוש ברור"
    app_version: str = "1.0.0"
    app_env: str = "development"

    # Database — defaults to SQLite for local dev
    database_url: str = "sqlite+aiosqlite:///./tlush_barur.db"

    # Security
    secret_key: str = "change-me-in-production-please"

    # Upload limits
    max_upload_size_mb: int = 20

    # Privacy / transient mode
    transient_ttl_hours: int = 1

    # Logging
    log_level: str = "INFO"


settings = Settings()
