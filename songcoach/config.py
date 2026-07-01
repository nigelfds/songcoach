"""Environment-driven application settings.

SongCoach is a local, single-user macOS app: SQLite on disk, files on the local
filesystem, and separation run inline in a background thread. No Postgres, Redis,
S3, or cloud services.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    secret_key: str = "dev-secret"
    debug: bool = True

    # Database (SQLite file next to the project).
    database_url: str = "sqlite:///./songcoach.db"

    # Storage: recordings and stems live on the local filesystem, served at /media.
    local_storage_dir: Path = Path("./data")

    # Native system-audio capture (macOS ScreenCaptureKit helper).
    # Path to the compiled `syscap` binary; relative paths resolve from repo root.
    syscap_bin: str = "native/syscap"

    # Demucs
    demucs_model: str = "htdemucs"
    stem_format: str = "mp3"
    max_duration_seconds: int = 600


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
