"""FastAPI application factory."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routes import api, pages

logging.basicConfig(level=logging.INFO)

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

# Create tables on boot (dev convenience; Heroku uses the release phase too).
init_db()

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
