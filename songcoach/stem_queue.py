"""A single-worker FIFO queue for the Demucs separation step.

Captures (manual or Apple Music mode) enqueue a job id and return immediately;
one daemon worker runs the slow separation one job at a time, so back-to-back
songs never spawn N concurrent Demucs runs. Not persisted — an interrupted job
is handled by the existing resume/rebuild path.
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger("songcoach.stem_queue")

_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()   # (job_id, task)
_worker: threading.Thread | None = None
_lock = threading.Lock()


def _run_job(job_id: str, task: str = "process") -> None:
    # Imported lazily so importing this module doesn't pull in the pipeline.
    from .pipeline.process import process_capture, reprocess_job
    if task == "reprocess":
        reprocess_job(job_id)
    else:
        process_capture(job_id)


def _worker_loop() -> None:
    while True:
        job_id, task = _queue.get()
        try:
            _run_job(job_id, task)
        except Exception:  # noqa: BLE001 — one bad job must not kill the worker
            log.exception("Stem worker failed for %s", job_id)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="stem-worker", daemon=True)
            _worker.start()


def enqueue(job_id: str) -> None:
    """Queue a captured job for separation. Returns immediately."""
    _ensure_worker()
    _queue.put((job_id, "process"))
    log.info("Enqueued %s for separation (queue depth ~%d)", job_id, _queue.qsize())


def enqueue_reprocess(job_id: str) -> None:
    """Queue a finished job to be re-separated into the current stem set."""
    _ensure_worker()
    _queue.put((job_id, "reprocess"))
    log.info("Enqueued %s for reprocess (queue depth ~%d)", job_id, _queue.qsize())
