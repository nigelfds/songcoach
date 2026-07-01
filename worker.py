"""RQ worker entrypoint. Run: `python worker.py` (or via the Procfile)."""
from __future__ import annotations

import os

# Belt-and-suspenders for macOS forking; also helps some libs on Linux.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import logging
import sys

from redis import Redis
from rq import Queue, SimpleWorker, Worker

from songcoach.config import settings
from songcoach.db import init_db

logging.basicConfig(level=logging.INFO)


def main() -> None:
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is not set; the worker needs Redis.")
    init_db()
    conn = Redis.from_url(settings.redis_url)
    queue = Queue("songcoach", connection=conn)

    # macOS aborts (SIGABRT) when RQ forks a work-horse and the child touches an
    # Obj-C framework. SimpleWorker runs the job in-process (no fork) — safe on
    # macOS. The heavy Demucs step is already its own subprocess, so we lose
    # little isolation. On Linux (Heroku) use the forking Worker for isolation.
    worker_cls = SimpleWorker if sys.platform == "darwin" else Worker
    logging.info("Starting %s on queue 'songcoach'", worker_cls.__name__)
    worker_cls([queue], connection=conn).work()


if __name__ == "__main__":
    main()
