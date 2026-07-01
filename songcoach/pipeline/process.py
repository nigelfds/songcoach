"""End-to-end job pipeline, safe to run in a worker or a background thread."""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from ..config import settings
from ..db import SessionLocal, engine
from ..models import Job, JobStatus, Track, TrackKind
from ..storage import get_storage
from . import downloader, separator

log = logging.getLogger("songcoach.pipeline")


def _set(session, job: Job, *, status=None, progress=None, error=None, **fields) -> None:
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if error is not None:
        job.error = error
    for k, v in fields.items():
        setattr(job, k, v)
    session.commit()


def _to_mp3(src: Path, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-codec:a", "libmp3lame", "-b:a", "256k", str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def run_job(job_id: str) -> None:
    """Download → separate → upload three stems → mark done."""
    # RQ forks a child process per job; drop any DB connections inherited from
    # the parent so this process opens its own (fork-safe for SQLite & Postgres).
    engine.dispose(close=False)

    session = SessionLocal()
    storage = get_storage()
    try:
        job = session.get(Job, job_id)
        if job is None:
            log.error("Job %s not found", job_id)
            return

        with tempfile.TemporaryDirectory(prefix="songcoach-") as tmp:
            work = Path(tmp)

            # 1. Download
            _set(session, job, status=JobStatus.downloading, progress=10)
            dl = downloader.download_audio(job.youtube_url, work / "download")
            _set(
                session, job,
                title=dl.title, duration_seconds=dl.duration, thumbnail_url=dl.thumbnail,
                progress=30,
            )

            if dl.duration and dl.duration > settings.max_duration_seconds:
                raise ValueError(
                    f"Track is {dl.duration:.0f}s; limit is {settings.max_duration_seconds}s"
                )

            # 2. Separate (the slow part)
            _set(session, job, status=JobStatus.separating, progress=45)
            sep = separator.separate(dl.audio_path, work / "separated")

            # 3. Prepare the full-song web track
            original_mp3 = work / "original.mp3"
            _to_mp3(dl.audio_path, original_mp3)

            # 4. Upload the three deliverables
            _set(session, job, status=JobStatus.uploading, progress=80)
            deliverables = {
                TrackKind.original: original_mp3,
                TrackKind.drums: sep.drums_path,
                TrackKind.no_drums: sep.no_drums_path,
            }
            job.tracks.clear()
            for kind, path in deliverables.items():
                key = f"jobs/{job.id}/{kind.value}.mp3"
                storage.save(path, key)
                job.tracks.append(
                    Track(kind=kind, storage_key=key, duration_seconds=dl.duration)
                )

            _set(session, job, status=JobStatus.done, progress=100, error=None)
            log.info("Job %s complete: %s", job_id, dl.title)

    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or str(exc)).strip()[-500:]
        log.exception("Job %s failed in subprocess", job_id)
        _fail(session, job_id, msg)
    except Exception as exc:  # noqa: BLE001 — record any failure for the UI
        log.exception("Job %s failed", job_id)
        _fail(session, job_id, str(exc))
    finally:
        session.close()


def _fail(session, job_id: str, message: str) -> None:
    job = session.get(Job, job_id)
    if job is not None:
        job.status = JobStatus.failed
        job.error = message
        session.commit()
