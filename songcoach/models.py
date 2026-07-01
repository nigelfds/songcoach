"""Database models: a Job (one captured recording) owns three Tracks."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    recording = "recording"
    queued = "queued"
    separating = "separating"
    uploading = "uploading"
    done = "done"
    failed = "failed"


class TrackKind(str, enum.Enum):
    original = "original"      # the full song
    drums = "drums"            # drums only
    no_drums = "no_drums"      # song without drums


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # User-supplied metadata for the recording.
    title: Mapped[str | None] = mapped_column(Text)          # song name
    artist: Mapped[str | None] = mapped_column(Text)
    youtube_url: Mapped[str | None] = mapped_column(Text)    # optional reference link
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.recording, nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    tracks: Mapped[list["Track"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[TrackKind] = mapped_column(Enum(TrackKind), nullable=False)
    # Storage key (local path relative to LOCAL_STORAGE_DIR).
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    job: Mapped[Job] = relationship(back_populates="tracks")
