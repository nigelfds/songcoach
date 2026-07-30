"""JSON sidecar metadata — the durable source of truth for a recording.

Each completed job writes a ``meta.json`` next to its stem files in the job's
output directory (``LOCAL_STORAGE_DIR/jobs/<id>/``). Together, the mp3 files and
this sidecar fully describe a recording; the SQLite DB is only a cache/index
over them and can be rebuilt at any time with ``python -m songcoach.rebuild``.

``schema_version`` is stamped into every sidecar so a future format change can
be detected and migrated.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .models import Job

SCHEMA_VERSION = 1
META_FILENAME = "meta.json"
THUMB_FILENAME = "thumbnail.jpg"


def job_dir(job_id: str) -> Path:
    return Path(settings.local_storage_dir) / "jobs" / job_id


def meta_path(job_id: str) -> Path:
    return job_dir(job_id) / META_FILENAME


def thumbnail_path(job_id: str) -> Path:
    return job_dir(job_id) / THUMB_FILENAME


def thumbnail_ref(job_id: str) -> tuple[str, int] | None:
    """(storage_key, version) for the job's thumbnail if on disk, else None.

    The version is the file mtime; append it as a query param so a refreshed
    thumbnail (same filename) busts the browser cache.
    """
    p = thumbnail_path(job_id)
    if p.exists():
        return f"jobs/{job_id}/{THUMB_FILENAME}", int(p.stat().st_mtime)
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def to_dict(job: Job) -> dict:
    data = {
        "schema_version": SCHEMA_VERSION,
        "id": job.id,
        "title": job.title,
        "artist": job.artist,
        "youtube_url": job.youtube_url,
        "duration_seconds": job.duration_seconds,
        "status": job.status.value,
        "error": job.error,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "tracks": [
            {
                "kind": t.kind.value,
                "file": Path(t.storage_key).name,
                "duration_seconds": t.duration_seconds,
            }
            for t in job.tracks
        ],
    }
    if thumbnail_path(job.id).exists():
        data["thumbnail"] = THUMB_FILENAME
    return data


def write_meta(job: Job) -> Path:
    """Write the job's sidecar atomically into its output directory."""
    path = meta_path(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(to_dict(job), indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic swap on POSIX
    return path


def mark_deleted(job_id: str) -> bool:
    """Soft-delete: set ``deleted: true`` in the job's sidecar, atomically.

    The stem files / thumbnail / capture on disk are left untouched. Returns
    ``False`` if there is no sidecar to mark.
    """
    path = meta_path(job_id)
    if not path.exists():
        return False
    data = read_meta(path)
    data["deleted"] = True
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic swap on POSIX
    return True


def read_meta(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
