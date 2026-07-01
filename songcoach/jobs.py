"""Run the separation pipeline for a job in a background thread.

Single-user local app: no queue. The request returns immediately and the slow
Demucs step runs in a daemon thread; the UI polls the job's status.
"""
from __future__ import annotations

import logging
import threading

from .pipeline.process import process_capture

log = logging.getLogger("songcoach.jobs")


def enqueue_processing(job_id: str) -> None:
    """Separate a captured recording into stems, off the request thread."""
    log.info("Running process_capture(%s) in background thread", job_id)
    threading.Thread(target=process_capture, args=(job_id,), daemon=True).start()
