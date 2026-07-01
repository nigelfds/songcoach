"""JSON API: create jobs, poll status, fetch track URLs."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_session
from ..jobs import enqueue
from ..models import Job, JobStatus
from ..storage import get_storage

router = APIRouter(prefix="/api", tags=["api"])

_YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|music\.youtube\.com/watch\?v=)[\w\-]+",
    re.IGNORECASE,
)


class CreateJobIn(BaseModel):
    url: str


class TrackOut(BaseModel):
    kind: str
    url: str
    duration_seconds: float | None


class JobOut(BaseModel):
    id: str
    status: str
    progress: int
    title: str | None
    duration_seconds: float | None
    thumbnail_url: str | None
    error: str | None
    tracks: list[TrackOut]


def _serialize(job: Job) -> JobOut:
    storage = get_storage()
    tracks = []
    if job.status == JobStatus.done:
        for t in job.tracks:
            tracks.append(
                TrackOut(kind=t.kind.value, url=storage.url(t.storage_key),
                         duration_seconds=t.duration_seconds)
            )
    return JobOut(
        id=job.id, status=job.status.value, progress=job.progress,
        title=job.title, duration_seconds=job.duration_seconds,
        thumbnail_url=job.thumbnail_url, error=job.error, tracks=tracks,
    )


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(payload: CreateJobIn, session: Session = Depends(get_session)):
    url = payload.url.strip()
    if not _YOUTUBE_RE.match(url):
        raise HTTPException(status_code=422, detail="Please provide a valid YouTube URL.")
    job = Job(youtube_url=url, status=JobStatus.queued)
    session.add(job)
    session.commit()
    enqueue(job.id)
    return _serialize(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(job)
