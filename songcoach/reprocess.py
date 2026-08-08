"""Reprocess the library into the current stem set (adds a vocals stem).

    python -m songcoach.reprocess           # every done job missing vocals
    python -m songcoach.reprocess --force    # even ones already reprocessed

Runs one song at a time (in this process), so it doesn't need the web server.
"""
from __future__ import annotations

import argparse
import logging

from .db import SessionLocal
from .models import Job, JobStatus, TrackKind
from .pipeline.process import reprocess_job
from .rebuild import rebuild

log = logging.getLogger("songcoach.reprocess")


def _has_vocals(job: Job) -> bool:
    return any(t.kind == TrackKind.vocals for t in job.tracks)


def run(force: bool = False) -> tuple[int, int, int]:
    """Reprocess done jobs. Returns (reprocessed, skipped, failed)."""
    rebuild(reset=True)   # index from disk first
    session = SessionLocal()
    try:
        candidates, skipped = [], 0
        for job in session.query(Job).filter(Job.status == JobStatus.done).all():
            if not force and _has_vocals(job):
                skipped += 1
            else:
                candidates.append(job.id)
    finally:
        session.close()

    done = failed = 0
    for jid in candidates:
        try:
            reprocess_job(jid)
            done += 1
        except Exception:  # noqa: BLE001
            failed += 1
            log.exception("Reprocess failed for %s", jid)
    log.info("Done — %d reprocessed, %d skipped, %d failed", done, skipped, failed)
    return done, skipped, failed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Reprocess the library to add a vocals stem.")
    parser.add_argument("--force", action="store_true",
                        help="reprocess even jobs that already have a vocals stem")
    args = parser.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
