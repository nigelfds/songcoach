"""Separation pipeline for a captured recording, run in a background thread."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import fetch_thumbnails, metadata
from ..config import settings
from ..db import SessionLocal
from ..models import Job, JobStatus, Track, TrackKind
from ..storage import get_storage
from . import separator
from .recorder import capture_dir

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
        [settings.ffmpeg_bin, "-y", "-i", str(src), "-codec:a", "libmp3lame",
         "-b:a", "256k", str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def _publish_stems(job: Job, storage, deliverables: dict) -> None:
    """storage.save each {TrackKind: path} into jobs/<id>/<kind>.mp3 and append a Track.

    Does NOT clear job.tracks — the caller clears first, then publishes.
    """
    for kind, path in deliverables.items():
        key = f"jobs/{job.id}/{kind.value}.mp3"
        storage.save(path, key)
        job.tracks.append(
            Track(kind=kind, storage_key=key, duration_seconds=job.duration_seconds)
        )


def process_capture(job_id: str) -> None:
    """Separate a captured recording into three stems → publish → mark done.

    The audio was already recorded to ``capture_dir(job_id)/capture.m4a`` by the
    recording session, so this picks up at separation (no download step).
    """
    session = SessionLocal()
    storage = get_storage()
    src_dir = capture_dir(job_id)
    source = src_dir / "capture.m4a"
    try:
        job = session.get(Job, job_id)
        if job is None:
            log.error("Job %s not found", job_id)
            return
        if not source.exists():
            raise RuntimeError(f"captured audio missing at {source}")

        # Fetch the YouTube thumbnail (if any) in parallel with separation, so it
        # shows in the library while the slow stem work runs.
        if job.youtube_url:
            fetch_thumbnails.refresh_job_thumbnail_async(job_id)

        with tempfile.TemporaryDirectory(prefix="songcoach-") as tmp:
            work = Path(tmp)

            # 1. Separate (the slow part)
            _set(session, job, status=JobStatus.separating, progress=40)
            sep = separator.separate(source, work / "separated")

            # 2. Prepare the full-recording web track
            original_mp3 = work / "original.mp3"
            _to_mp3(source, original_mp3)

            # 3. Upload the four deliverables
            _set(session, job, status=JobStatus.uploading, progress=80)
            job.tracks.clear()
            _publish_stems(job, storage, {
                TrackKind.original: original_mp3,
                TrackKind.drums: sep.drums_path,
                TrackKind.vocals: sep.vocals_path,
                TrackKind.no_drums_no_vocals: sep.backing_path,
            })

            _set(session, job, status=JobStatus.done, progress=100, error=None)
            # Write the JSON sidecar — the durable source of truth alongside the
            # stems; the SQLite row is just a cache of this.
            metadata.write_meta(job)
            log.info("Job %s complete: %s", job_id, job.title)

        # Reclaim the raw capture now that the stems are published.
        shutil.rmtree(src_dir, ignore_errors=True)

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
        # Persist the failure to the durable sidecar so it survives the startup
        # rebuild (which re-indexes from jobs/*/meta.json) as a failed, retryable job.
        try:
            metadata.write_meta(job)
        except OSError:
            log.warning("Could not write failure sidecar for %s", job_id, exc_info=True)


def reprocess_job(job_id: str) -> None:
    """Re-separate a finished job from its retained original.mp3 into the current
    stem set (drums/vocals/backing), replacing the legacy no_drums stem. The
    original track + markers are preserved."""
    session = SessionLocal()
    storage = get_storage()
    pub_dir = metadata.job_dir(job_id)
    original = pub_dir / "original.mp3"
    try:
        job = session.get(Job, job_id)
        if job is None:
            log.error("Reprocess: job %s not found", job_id)
            return
        if not original.exists():
            raise RuntimeError(f"original audio missing at {original}")

        _set(session, job, status=JobStatus.separating, progress=40)
        with tempfile.TemporaryDirectory(prefix="songcoach-re-") as tmp:
            sep = separator.separate(original, Path(tmp) / "separated")
            _set(session, job, status=JobStatus.uploading, progress=80)
            job.tracks.clear()
            job.tracks.append(Track(
                kind=TrackKind.original,
                storage_key=f"jobs/{job_id}/original.mp3",
                duration_seconds=job.duration_seconds,
            ))
            _publish_stems(job, storage, {
                TrackKind.drums: sep.drums_path,
                TrackKind.vocals: sep.vocals_path,
                TrackKind.no_drums_no_vocals: sep.backing_path,
            })
            (pub_dir / "no_drums.mp3").unlink(missing_ok=True)   # drop legacy stem
            _set(session, job, status=JobStatus.done, progress=100, error=None)
            metadata.write_meta(job)   # preserves markers/deleted
            log.info("Reprocessed %s: %s", job_id, job.title)
    except subprocess.CalledProcessError as exc:
        _fail(session, job_id, (exc.stderr or str(exc)).strip()[-500:])
    except Exception as exc:  # noqa: BLE001
        log.exception("Reprocess %s failed", job_id)
        _fail(session, job_id, str(exc))
    finally:
        session.close()
