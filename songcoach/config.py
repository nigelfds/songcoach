"""Environment-driven application settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    secret_key: str = "dev-secret"
    debug: bool = True

    # Database. Heroku hands out postgres:// which SQLAlchemy wants as postgresql+psycopg://
    database_url: str = "sqlite:///./songcoach.db"

    # Jobs
    redis_url: str | None = None
    run_jobs_inline: bool = False

    # Storage
    storage_backend: str = "local"  # "local" | "s3"
    local_storage_dir: Path = Path("./data")
    s3_bucket: str | None = None
    aws_region: str = "us-east-1"
    s3_signed_url_ttl: int = 3600

    # yt-dlp cookies (for YouTube Premium / ad-free). Precedence:
    #   file  >  raw contents  >  read from local browser
    ytdlp_cookies_file: str | None = None
    ytdlp_cookies: str | None = None            # raw contents (Heroku config var)
    ytdlp_cookies_from_browser: str | None = None  # e.g. "chrome", "safari", "firefox"

    # Demucs
    demucs_model: str = "htdemucs"
    stem_format: str = "mp3"
    max_duration_seconds: int = 600

    @property
    def normalized_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def use_queue(self) -> bool:
        return bool(self.redis_url) and not self.run_jobs_inline


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
