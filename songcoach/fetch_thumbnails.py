"""Fetch YouTube thumbnails for recordings that have a youtube_url.

Loads the SQLite index from the data folder (a non-destructive rebuild), then
for every recording with a ``youtube_url`` downloads its thumbnail into that
job's output directory (``data/jobs/<id>/thumbnail.jpg``) and notes it in the
job's ``meta.json``.

    python -m songcoach.fetch_thumbnails [--force]

Uses only YouTube's public image CDN (img.youtube.com) — no yt-dlp, no API key.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import rebuild
from .db import SessionLocal
from .metadata import META_FILENAME, THUMB_FILENAME, job_dir, thumbnail_path, write_meta
from .models import Job
from .youtube import video_id

log = logging.getLogger("songcoach.thumbnails")

# Best → worst; a missing maxres/sd returns a tiny gray placeholder, so we also
# gate on byte size below.
_QUALITIES = ["maxresdefault", "sddefault", "hqdefault"]


def _download(url: str) -> bytes | None:
    req = Request(url, headers={"User-Agent": "SongCoach/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except (HTTPError, URLError, TimeoutError):
        return None


def fetch_thumbnail(vid: str) -> bytes | None:
    for quality in _QUALITIES:
        data = _download(f"https://img.youtube.com/vi/{vid}/{quality}.jpg")
        # The "not available" placeholder is a ~1-2 KB gray image; skip it.
        if data and len(data) > 2000:
            return data
    return None


_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB cap for user-supplied thumbnails


def _download_image(url: str, max_bytes: int = _MAX_IMAGE_BYTES) -> bytes | None:
    """Download a user-supplied image URL, bounded by content-type + size."""
    req = Request(url, headers={"User-Agent": "SongCoach/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return None
            ctype = resp.headers.get_content_type()
            if not ctype.startswith("image/"):
                log.warning("Not an image (%s): %s", ctype, url)
                return None
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                log.warning("Image too large (> %d bytes): %s", max_bytes, url)
                return None
            return data
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def store_image_from_url(job_id: str, image_url: str) -> None:
    """Fetch an image URL and store it as the job's thumbnail (best-effort).

    Writes only the image file — not the sidecar; the job is still ``recording``
    here, and ``meta.json`` gets written later (process success / _fail), with
    ``to_dict`` picking up the thumbnail when the file exists.
    """
    data = _download_image(image_url)
    if not data:
        return
    dest = thumbnail_path(job_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    log.info("Stored thumbnail for %s from %s (%d KB)", job_id, image_url, len(data) // 1024)


def store_image_from_url_async(job_id: str, image_url: str) -> None:
    threading.Thread(target=store_image_from_url, args=(job_id, image_url), daemon=True).start()


def _note_in_meta(dir_: Path, filename: str) -> None:
    meta = dir_ / META_FILENAME
    if not meta.exists():
        return
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except ValueError:
        return
    data["thumbnail"] = filename
    tmp = meta.with_name(meta.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(meta)


def run(*, force: bool = False) -> int:
    """Fetch missing thumbnails. Returns the number downloaded."""
    # Sync the DB from the data folder first (non-destructive).
    rebuild.rebuild(reset=False)

    session = SessionLocal()
    fetched = 0
    try:
        jobs = session.query(Job).filter(Job.youtube_url.isnot(None)).all()
        for job in jobs:
            d = job_dir(job.id)
            if not d.is_dir():
                continue
            dest = d / THUMB_FILENAME
            if dest.exists() and not force:
                log.info("%s: thumbnail already present, skipping", job.title)
                continue
            vid = video_id(job.youtube_url or "")
            if not vid:
                log.warning("%s: can't parse a video id from %s", job.title, job.youtube_url)
                continue
            data = fetch_thumbnail(vid)
            if not data:
                log.warning("%s: no thumbnail available for %s", job.title, vid)
                continue
            dest.write_bytes(data)
            _note_in_meta(d, THUMB_FILENAME)
            fetched += 1
            log.info("%s: saved %s (%d KB)", job.title, dest, len(data) // 1024)
    finally:
        session.close()

    log.info("Done — %d thumbnail(s) fetched", fetched)
    return fetched


def refresh_job_thumbnail(job_id: str) -> None:
    """Re-sync one job's thumbnail to its current youtube_url, in its own session.

    Clears any stale image first (the old one no longer matches a changed URL),
    fetches a fresh one if a valid URL is set, then rewrites the sidecar so its
    ``thumbnail`` key tracks the file.
    """
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job is None:
            return
        thumbnail_path(job_id).unlink(missing_ok=True)
        vid = video_id(job.youtube_url or "")
        if vid:
            data = fetch_thumbnail(vid)
            if data:
                dest = thumbnail_path(job_id)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                log.info("Refreshed thumbnail for %s (%d KB)", job_id, len(data) // 1024)
            else:
                log.warning("No thumbnail available for %s (%s)", job_id, job.youtube_url)
        write_meta(job)
    finally:
        session.close()


def refresh_job_thumbnail_async(job_id: str) -> None:
    threading.Thread(target=refresh_job_thumbnail, args=(job_id,), daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Fetch YouTube thumbnails into job folders.")
    parser.add_argument(
        "--force", action="store_true",
        help="re-download even if a thumbnail already exists",
    )
    args = parser.parse_args()
    n = run(force=args.force)
    print(f"Fetched {n} thumbnail(s).")


if __name__ == "__main__":
    main()
