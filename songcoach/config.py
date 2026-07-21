"""Environment-driven application settings.

SongCoach is a local, single-user macOS app: SQLite on disk, files on the local
filesystem, and separation run inline in a background thread. No Postgres, Redis,
S3, or cloud services.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import paths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    secret_key: str = "dev-secret"
    debug: bool = True

    # Database (SQLite). Dev: ./songcoach.db; frozen: Application Support. Env
    # (DATABASE_URL) still wins over the default when set.
    database_url: str = Field(default_factory=paths.database_url)

    # Storage: recordings and stems live on the local filesystem, served at /media.
    # Dev: ./data; frozen: ~/Library/Application Support/SongCoach/data.
    local_storage_dir: Path = Field(default_factory=paths.data_dir)

    # Native system-audio capture (macOS ScreenCaptureKit helper).
    # Path to the compiled `syscap` binary; relative paths resolve from the
    # resource dir (repo root in dev, the bundle when frozen).
    syscap_bin: str = "native/syscap"

    # External media tools. Dev: found on PATH; frozen: bundled next to the app.
    ffmpeg_bin: str = Field(default_factory=paths.ffmpeg_bin)
    ffprobe_bin: str = Field(default_factory=paths.ffprobe_bin)

    # Demucs
    demucs_model: str = "htdemucs"
    stem_format: str = "mp3"
    max_duration_seconds: int = 600


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
