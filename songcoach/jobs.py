"""Dispatch a job to the RQ worker, or run it inline for local dev."""
from __future__ import annotations

import logging
import threading

from .config import settings
from .pipeline.process import run_job

log = logging.getLogger("songcoach.jobs")

_QUEUE_NAME = "songcoach"


def _get_queue():
    from redis import Redis
    from rq import Queue

    conn = Redis.from_url(settings.redis_url)
    return Queue(_QUEUE_NAME, connection=conn)


def enqueue(job_id: str) -> None:
    """Kick off processing for a job."""
    if settings.use_queue:
        queue = _get_queue()
        queue.enqueue(run_job, job_id, job_timeout=1800)
        log.info("Enqueued job %s on RQ", job_id)
    else:
        # Inline mode: run in a daemon thread so the request returns immediately.
        log.info("Running job %s inline (no queue)", job_id)
        threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
