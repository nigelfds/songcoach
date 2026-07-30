"""Rebuild the SQLite cache from the JSON metadata sidecars on disk.

The stem files and their ``meta.json`` under ``LOCAL_STORAGE_DIR/jobs/<id>/`` are
the source of truth; the SQLite database is a disposable index over them. This
scans that folder and recreates the ``jobs`` / ``tracks`` rows.

    python -m songcoach.rebuild            # drop + recreate from disk
    python -m songcoach.rebuild --merge    # upsert without dropping existing rows

Tracks are derived from the mp3 files that are actually present (the files win),
so a sidecar that references a deleted stem won't resurrect a phantom track.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from .config import settings
from .db import Base, SessionLocal, engine, init_db
from .metadata import META_FILENAME, read_meta
from .models import Job, JobStatus, Track, TrackKind

log = logging.getLogger("songcoach.rebuild")


def _is_soft_deleted(job_id: str) -> bool:
    """Check if a job's sidecar has the soft-delete flag set."""
    meta = Path(settings.local_storage_dir) / "jobs" / job_id / META_FILENAME
    if not meta.exists():
        return False
    try:
        return bool(read_meta(meta).get("deleted"))
    except (ValueError, OSError):
        return False


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _job_from_meta(data: dict, job_dir: Path) -> Job | None:
    job_id = data.get("id") or job_dir.name
    try:
        status = JobStatus(data.get("status", "done"))
    except ValueError:
        status = JobStatus.done

    job = Job(
        id=job_id,
        title=data.get("title"),
        artist=data.get("artist"),
        youtube_url=data.get("youtube_url"),
        duration_seconds=data.get("duration_seconds"),
        status=status,
        progress=100 if status == JobStatus.done else 0,
        error=data.get("error"),
        created_at=_parse_dt(data.get("created_at")),
        updated_at=_parse_dt(data.get("updated_at")),
    )

    # Duration hints from the sidecar, keyed by kind; fall back to job duration.
    meta_durations = {
        t.get("kind"): t.get("duration_seconds") for t in data.get("tracks", [])
    }
    for kind in TrackKind:
        mp3 = job_dir / f"{kind.value}.mp3"
        if mp3.exists():
            job.tracks.append(
                Track(
                    kind=kind,
                    storage_key=f"jobs/{job_id}/{kind.value}.mp3",
                    duration_seconds=meta_durations.get(kind.value, job.duration_seconds),
                )
            )
    return job


def _index_orphan_captures(session, indexed_ids: set[str]) -> int:
    """Index captures in recordings/ that have no jobs/ entry as resumable failed jobs."""
    rec_root = Path(settings.local_storage_dir) / "recordings"
    if not rec_root.is_dir():
        return 0
    count = 0
    for capture in sorted(rec_root.glob("*/capture.m4a")):
        job_id = capture.parent.name
        if job_id in indexed_ids or session.get(Job, job_id) is not None:
            continue
        if _is_soft_deleted(job_id):
            continue   # soft-deleted → don't resurrect from a lingering capture
        mtime = datetime.fromtimestamp(capture.stat().st_mtime).astimezone()
        session.merge(Job(
            id=job_id,
            title=f"Untitled recording {mtime:%b %-d, %-I:%M %p}",
            status=JobStatus.failed,
            progress=0,
            error="Stemming didn't finish — retry to resume.",
            created_at=mtime,
        ))
        count += 1
    return count


def rebuild(*, reset: bool = True) -> int:
    """Recreate DB rows from disk. Returns the number of jobs loaded."""
    if reset:
        log.info("Dropping existing tables")
        Base.metadata.drop_all(bind=engine)
    init_db()

    jobs_root = Path(settings.local_storage_dir) / "jobs"
    if not jobs_root.is_dir():
        log.warning("No jobs directory at %s; scanning for orphan captures only", jobs_root)

    session = SessionLocal()
    count = 0
    try:
        indexed_ids: set[str] = set()
        for meta_file in sorted(jobs_root.glob(f"*/{META_FILENAME}")):
            try:
                data = read_meta(meta_file)
            except (ValueError, OSError) as exc:
                log.warning("Skipping unreadable %s: %s", meta_file, exc)
                continue
            if data.get("deleted"):
                continue   # soft-deleted → not indexed, absent from the library
            job = _job_from_meta(data, meta_file.parent)
            if job is None:
                continue
            # merge() upserts by primary key so --merge can run repeatedly.
            session.merge(job)
            indexed_ids.add(job.id)
            count += 1
        count += _index_orphan_captures(session, indexed_ids)
        session.commit()
    finally:
        session.close()

    log.info("Rebuilt %d job(s) from %s", count, Path(settings.local_storage_dir))
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Rebuild the SQLite cache from disk.")
    parser.add_argument(
        "--merge", action="store_true",
        help="upsert into the existing DB instead of dropping it first",
    )
    args = parser.parse_args()
    n = rebuild(reset=not args.merge)
    print(f"Loaded {n} recording(s) into {settings.database_url}")


if __name__ == "__main__":
    main()
