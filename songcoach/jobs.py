"""Dispatch the separation pipeline for a job.

Single-user local app: captures enqueue onto a single-worker serial queue
(see stem_queue) so back-to-back songs stem one at a time.
"""
from __future__ import annotations

import logging

from . import stem_queue

log = logging.getLogger("songcoach.jobs")


def enqueue_processing(job_id: str) -> None:
    """Queue a captured recording for separation, off the request thread."""
    log.info("Enqueuing separation for %s", job_id)
    stem_queue.enqueue(job_id)
