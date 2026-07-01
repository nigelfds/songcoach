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

app = FastAPI(title="SongCoach", debug=settings.debug)

# Create tables on boot (dev convenience; Heroku uses the release phase too).
init_db()

app.include_router(api.router)
app.include_router(pages.router)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Serve stored recordings/stems straight off disk.
media_dir = Path(settings.local_storage_dir)
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version}
