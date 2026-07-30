"""Apple Music mode: drive per-song capture from Music transport events.

Consumes MusicState samples (one per watcher poll) and diffs against the
previous phase to begin/pause/resume/finalize song captures, dispatching each
finished song (>= min length) to the serial stem queue. A song is one Job made
of 1+ SegmentedRecorder segments.
"""
from __future__ import annotations

import logging
import shutil
import threading

from .. import metadata, recording, stem_queue
from ..config import settings
from ..db import SessionLocal
from ..models import Job, JobStatus
from ..pipeline.recorder import RecorderError, capture_dir
from ..pipeline.segmented_recorder import SegmentedRecorder
from . import artwork
from .watcher import MusicState

log = logging.getLogger("songcoach.apple_music.session")


class AppleMusicSession:
    def __init__(self, *, min_song_seconds: int = 5):
        self._min = min_song_seconds
        self._lock = threading.Lock()
        self._active = False
        self._phase = "armed"                 # armed | capturing | paused
        self._job_id: str | None = None
        self._track_id: str | None = None
        self._recorder: SegmentedRecorder | None = None
        self._current: dict | None = None     # {"name","artist"}
        self._captured: list[dict] = []        # dispatched songs, newest first

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            recording.set_apple_music_active(True)
            self._active = True
            self._phase = "armed"
        log.info("Apple Music mode started")

    def stop(self) -> None:
        with self._lock:
            try:
                if self._recorder is not None:
                    self._finalize_current()
            finally:
                self._active = False
                self._phase = "armed"
                recording.set_apple_music_active(False)
        log.info("Apple Music mode stopped")

    def status(self) -> dict:
        with self._lock:
            active = self._active
            phase = self._phase
            current = dict(self._current) if phase in ("capturing", "paused") else None
            captured = list(self._captured)
        # Enrich each dispatched song with its live separation state (queued →
        # separating → done) so the UI can show stem progress. Done outside the
        # lock so the DB reads don't block the watcher thread's on_state().
        return {
            "active": active,
            "phase": phase,
            "current": current,
            "captured": self._with_job_state(captured),
        }

    def _with_job_state(self, captured: list[dict]) -> list[dict]:
        if not captured:
            return []
        session = SessionLocal()
        try:
            out = []
            for c in captured:
                job = session.get(Job, c["job_id"])
                out.append({
                    **c,
                    "status": job.status.value if job is not None else "unknown",
                    "progress": job.progress if job is not None else 0,
                })
            return out
        finally:
            session.close()

    # ---- event handling --------------------------------------------------
    def on_state(self, s: MusicState) -> None:
        with self._lock:
            if not self._active:
                return
            if s.state == "playing":
                if self._phase == "armed":
                    self._begin_song(s)
                elif self._phase == "paused":
                    if s.track_id == self._track_id:
                        self._resume_song()
                    else:
                        self._finalize_current()
                        self._begin_song(s)
                elif self._phase == "capturing":
                    if s.track_id != self._track_id:
                        self._finalize_current()
                        self._begin_song(s)
            elif s.state == "paused":
                if self._phase == "capturing":
                    self._pause_song()
            elif s.state in ("stopped", "closed"):
                if self._phase in ("capturing", "paused"):
                    self._finalize_current()
                    self._phase = "armed"

    # ---- actions ---------------------------------------------------------
    def _begin_song(self, s: MusicState) -> None:
        job_id = self._create_job(s.name or "Untitled", s.artist)
        recorder = SegmentedRecorder(capture_dir(job_id),
                                     max_seconds=settings.max_duration_seconds)
        try:
            recorder.start()
        except RecorderError as exc:
            log.error("Could not start capture for %s: %s", job_id, exc)
            self._mark_failed(job_id, str(exc))
            self._phase = "armed"
            self._job_id = self._recorder = self._current = self._track_id = None
            return
        self._job_id = job_id
        self._track_id = s.track_id
        self._recorder = recorder
        self._current = {"name": s.name, "artist": s.artist}
        self._phase = "capturing"
        artwork.fetch_artwork_async(job_id)
        log.info("Capturing '%s' — %s (%s)", s.name, s.artist, job_id)

    def _pause_song(self) -> None:
        self._recorder.pause()
        self._phase = "paused"

    def _resume_song(self) -> None:
        self._recorder.resume()
        self._phase = "capturing"

    def _finalize_current(self) -> None:
        recorder, job_id, current = self._recorder, self._job_id, self._current
        self._recorder = self._job_id = self._current = self._track_id = None
        if recorder is None or job_id is None:
            return
        try:
            result = recorder.finish()
        except RecorderError as exc:
            log.warning("Finalize failed for %s: %s", job_id, exc)
            self._discard_job(job_id)
            return
        duration = result.duration or 0.0
        if duration < self._min:
            log.info("Discarding short song %s (%.1fs < %ds)", job_id, duration, self._min)
            self._discard_job(job_id)
            return
        self._stamp_and_enqueue(job_id, duration)
        self._captured.insert(0, {"job_id": job_id,
                                  "title": (current or {}).get("name"),
                                  "artist": (current or {}).get("artist")})

    # ---- persistence helpers --------------------------------------------
    def _create_job(self, title: str, artist: str | None) -> str:
        session = SessionLocal()
        try:
            job = Job(title=title, artist=artist, status=JobStatus.recording, progress=0)
            session.add(job)
            session.commit()
            return job.id
        finally:
            session.close()

    def _stamp_and_enqueue(self, job_id: str, duration: float) -> None:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.duration_seconds = duration
            job.status = JobStatus.queued
            job.progress = 10
            session.commit()
        finally:
            session.close()
        stem_queue.enqueue(job_id)

    def _mark_failed(self, job_id: str, message: str) -> None:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.failed
                job.error = message
                session.commit()
        finally:
            session.close()

    def _discard_job(self, job_id: str) -> None:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is not None:
                session.delete(job)
                session.commit()
        finally:
            session.close()
        shutil.rmtree(capture_dir(job_id), ignore_errors=True)
        shutil.rmtree(metadata.job_dir(job_id), ignore_errors=True)
