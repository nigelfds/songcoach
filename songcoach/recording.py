"""Manage the single active system-audio capture and turn it into a job.

This is a local, single-user desktop app, so there is at most one recording in
flight. We hold that live ``Recorder`` in a module-level slot guarded by a lock:
``start()`` creates a queued Job and begins capturing; ``stop()`` finalises the
audio, stamps the Job, and hands it to the separation pipeline.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from . import jobs
from .config import settings
from .db import SessionLocal
from .models import Job, JobStatus
from .pipeline.recorder import Recorder, RecorderError, capture_dir

log = logging.getLogger("songcoach.recording")

_lock = threading.Lock()
_active: dict | None = None  # {"job_id": str, "recorder": Recorder}


def is_recording() -> bool:
    return _active is not None


def start(*, title: str, artist: str | None = None, youtube_url: str | None = None) -> str:
    """Begin a capture with its metadata and return the new job id."""
    global _active
    with _lock:
        if _active is not None:
            raise RecorderError("A recording is already in progress")

        session = SessionLocal()
        try:
            job = Job(
                title=title, artist=artist, youtube_url=youtube_url,
                status=JobStatus.recording, progress=0,
            )
            session.add(job)
            session.commit()
            job_id = job.id
        finally:
            session.close()

        recorder = Recorder(capture_dir(job_id), max_seconds=settings.max_duration_seconds)
        try:
            recorder.start()
        except RecorderError as exc:
            _mark_failed(job_id, str(exc))
            raise

        _active = {"job_id": job_id, "recorder": recorder}
        log.info("Recording started for job %s", job_id)
        return job_id


def stop() -> str:
    """Finalise the active capture, kick off separation, return the job id."""
    global _active
    with _lock:
        if _active is None:
            raise RecorderError("No recording in progress")
        job_id = _active["job_id"]
        recorder = _active["recorder"]
        try:
            result = recorder.stop()
        except RecorderError as exc:
            _active = None
            _mark_failed(job_id, str(exc))
            raise
        _active = None

    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        job.title = job.title or f"Recording {datetime.now():%b %-d, %-I:%M %p}"
        job.duration_seconds = result.duration
        job.status = JobStatus.queued
        job.progress = 10
        session.commit()
    finally:
        session.close()

    log.info("Recording stopped for job %s (%.1fs); queuing separation",
             job_id, result.duration or 0.0)
    jobs.enqueue_processing(job_id)
    return job_id


def _mark_failed(job_id: str, message: str) -> None:
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.failed
            job.error = message
            session.commit()
    finally:
        session.close()
