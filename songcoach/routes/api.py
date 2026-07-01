"""JSON API: drive recordings, poll status, fetch track URLs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import recording
from ..db import get_session
from ..models import Job, JobStatus
from ..pipeline.recorder import RecorderError
from ..storage import get_storage

router = APIRouter(prefix="/api", tags=["api"])


class StartRecordingIn(BaseModel):
    title: str
    artist: str | None = None
    youtube_url: str | None = None


class TrackOut(BaseModel):
    kind: str
    url: str
    duration_seconds: float | None


class JobOut(BaseModel):
    id: str
    status: str
    progress: int
    title: str | None
    artist: str | None
    youtube_url: str | None
    duration_seconds: float | None
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
        title=job.title, artist=job.artist, youtube_url=job.youtube_url,
        duration_seconds=job.duration_seconds, error=job.error, tracks=tracks,
    )


@router.post("/recordings/start", response_model=JobOut, status_code=201)
def start_recording(payload: StartRecordingIn, session: Session = Depends(get_session)):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="A song name is required.")
    try:
        job_id = recording.start(
            title=title,
            artist=(payload.artist or "").strip() or None,
            youtube_url=(payload.youtube_url or "").strip() or None,
        )
    except RecorderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _serialize(session.get(Job, job_id))


@router.post("/recordings/stop", response_model=JobOut)
def stop_recording(session: Session = Depends(get_session)):
    try:
        job_id = recording.stop()
    except RecorderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _serialize(session.get(Job, job_id))


@router.get("/recordings/status")
def recording_status():
    return {"recording": recording.is_recording()}


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(session: Session = Depends(get_session)):
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc())).all()
    return [_serialize(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(job)
