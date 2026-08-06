"""JSON API: drive recordings, poll status, fetch track URLs."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import archive, fetch_thumbnails, jobs, metadata, recording, youtube
from ..rebuild import rebuild
from ..apple_music import service as apple_music_service
from ..db import get_session, SessionLocal
from ..models import Job, JobStatus
from ..pipeline.recorder import RecorderError, capture_dir
from ..storage import get_storage

router = APIRouter(prefix="/api", tags=["api"])


class YouTubeMetaOut(BaseModel):
    video_id: str
    canonical_url: str
    embed_url: str
    title: str
    song: str
    artist: str | None


class StartRecordingIn(BaseModel):
    title: str
    artist: str | None = None
    youtube_url: str | None = None
    image_url: str | None = None


class UpdateJobIn(BaseModel):
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
    thumbnail_url: str | None
    duration_seconds: float | None
    error: str | None
    resumable: bool
    tracks: list[TrackOut]


class MarkerIn(BaseModel):
    id: str = Field(max_length=64)
    time: float = Field(ge=0)
    name: str = Field(default="", max_length=120)


class MarkersIn(BaseModel):
    markers: list[MarkerIn] = Field(max_length=200)


def _serialize(job: Job) -> JobOut:
    storage = get_storage()
    tracks = []
    if job.status == JobStatus.done:
        for t in job.tracks:
            tracks.append(
                TrackOut(kind=t.kind.value, url=storage.url(t.storage_key),
                         duration_seconds=t.duration_seconds)
            )
    thumb = metadata.thumbnail_ref(job.id)
    thumbnail_url = f"{storage.url(thumb[0])}?v={thumb[1]}" if thumb else None
    resumable = (
        job.status == JobStatus.failed
        and (capture_dir(job.id) / "capture.m4a").exists()
    )
    return JobOut(
        id=job.id, status=job.status.value, progress=job.progress,
        title=job.title, artist=job.artist, youtube_url=job.youtube_url,
        thumbnail_url=thumbnail_url,
        duration_seconds=job.duration_seconds, error=job.error,
        resumable=resumable, tracks=tracks,
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
    image_url = (payload.image_url or "").strip()
    if image_url:
        fetch_thumbnails.store_image_from_url_async(job_id, image_url)
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


@router.get("/youtube/meta", response_model=YouTubeMetaOut)
def youtube_meta(url: str):
    """Clean a pasted YouTube URL and pull its title/artist for the form."""
    info = youtube.lookup(url)
    if not info:
        raise HTTPException(status_code=422, detail="That doesn't look like a YouTube link.")
    return YouTubeMetaOut(
        video_id=info["video_id"],
        canonical_url=info["canonical_url"],
        embed_url=info["embed_url"],
        title=info["title"],
        song=info["song"],
        artist=info["artist"] or None,
    )


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


@router.patch("/jobs/{job_id}", response_model=JobOut)
def update_job(job_id: str, payload: UpdateJobIn, session: Session = Depends(get_session)):
    """Edit a recording's metadata; writes through to the sidecar and DB.

    If the YouTube URL changed, refresh the thumbnail in the background.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="A song name is required.")

    old_url = job.youtube_url
    job.title = title
    job.artist = (payload.artist or "").strip() or None
    job.youtube_url = (payload.youtube_url or "").strip() or None
    session.commit()

    # Keep the sidecar (source of truth) in step with the DB cache.
    metadata.write_meta(job)

    if job.youtube_url != old_url:
        fetch_thumbnails.refresh_job_thumbnail_async(job_id)

    return _serialize(job)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    """Soft-delete a recording: flag its sidecar and drop it from the index.

    The files on disk are left untouched. Its own session is closed before the
    rebuild so the drop-and-recreate doesn't race an open transaction.
    """
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        status = job.status if job is not None else None
    finally:
        session.close()

    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if status not in (JobStatus.done, JobStatus.failed):
        raise HTTPException(status_code=409, detail="Can't delete while it's still processing.")
    if not metadata.mark_deleted(job_id):
        raise HTTPException(status_code=404, detail="Recording not found on disk")

    rebuild(reset=True)   # refresh the index — the deleted item drops out
    return Response(status_code=204)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, session: Session = Depends(get_session)):
    """Re-run separation for a failed job whose capture is still on disk."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    if job.status != JobStatus.failed:
        raise HTTPException(status_code=409, detail="Only failed recordings can be retried.")
    if not (capture_dir(job_id) / "capture.m4a").exists():
        raise HTTPException(status_code=409, detail="This recording is no longer available.")
    job.status = JobStatus.queued
    job.progress = 10
    job.error = None
    session.commit()
    jobs.enqueue_processing(job_id)
    return _serialize(job)


@router.get("/jobs/{job_id}/markers")
def get_markers(job_id: str):
    if not metadata.meta_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"markers": metadata.read_markers(job_id)}


@router.put("/jobs/{job_id}/markers")
def put_markers(job_id: str, payload: MarkersIn):
    markers = []
    for m in payload.markers:
        markers.append({"id": m.id, "time": m.time, "name": m.name.strip()})
    if not metadata.write_markers(job_id, markers):
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"markers": markers}


@router.get("/export")
def export_data():
    """Download the whole data/ library as a .zip."""
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        archive.build_export(Path(tmp))
    except Exception:
        os.unlink(tmp)
        raise
    filename = f"SongCoach-export-{date.today():%Y%m%d}.zip"
    return FileResponse(
        tmp, media_type="application/zip", filename=filename,
        background=BackgroundTask(os.unlink, tmp),
    )


@router.post("/import")
def import_data(file: UploadFile):
    """Merge an uploaded .zip into the library (cp -rf) and rebuild the cache."""
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out)
        result = archive.import_archive(Path(tmp))
    except archive.ArchiveError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        os.unlink(tmp)
    return {"added": result.added, "updated": result.updated}


@router.post("/apple-music/start")
def apple_music_start():
    try:
        return apple_music_service.start_mode()
    except apple_music_service.ModeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/apple-music/stop")
def apple_music_stop():
    return apple_music_service.stop_mode()


@router.get("/apple-music/status")
def apple_music_status():
    return apple_music_service.status()
