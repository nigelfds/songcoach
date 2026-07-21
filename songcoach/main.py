"""FastAPI application factory."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import paths
from .config import settings
from .rebuild import rebuild
from .routes import api, pages

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("songcoach")

# Put bundled binaries on PATH + point torch at bundled weights (frozen only).
paths.setup_runtime()

BASE_DIR = Path(__file__).resolve().parent


class NoCacheStaticFiles(StaticFiles):
    """Serve app assets with revalidation so edits always take effect.

    Starlette's StaticFiles sends ETag/Last-Modified but no Cache-Control, so
    browsers fall back to heuristic caching and can keep running a stale JS/CSS
    after a change (e.g. a reworked app.js whose new buttons never get wired up).
    `no-cache` still stores the file but forces a conditional request each load —
    a cheap 304 when unchanged, fresh bytes the moment it changes.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response

app = FastAPI(title="SongCoach", debug=settings.debug)

# The stem folders + their meta.json sidecars are the source of truth; the
# SQLite database is a disposable index. Rebuild it from disk on every launch so
# a shipped/updated app starts from an empty cache and reindexes whatever data is
# actually present (and schema changes are a free drop-and-recreate).
paths.ensure_dirs()
log.info("Rebuilt cache: %d recording(s) from %s", rebuild(), settings.local_storage_dir)

app.include_router(api.router)
app.include_router(pages.router)

app.mount("/static", NoCacheStaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Serve stored recordings/stems straight off disk.
media_dir = Path(settings.local_storage_dir)
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version}
