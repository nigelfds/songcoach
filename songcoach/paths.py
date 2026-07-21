"""Filesystem locations, resolved for both dev and a frozen (PyInstaller) app.

Dev (running from the repo):
  - data + DB cache live in the working directory (./data, ./songcoach.db) — the
    behaviour that's always been in place.
  - bundled binaries (syscap, ffmpeg) resolve from the repo / PATH.

Frozen (.app built with PyInstaller):
  - user data and the rebuilt DB cache live in
    ~/Library/Application Support/SongCoach/  (writable; the .app itself is read-only).
  - bundled binaries live inside the app, under sys._MEIPASS.

Keeping this in one place means the rest of the code doesn't care which mode it's
running in.
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "SongCoach"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only bundled assets / helper binaries (syscap, ffmpeg, …)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    # Dev: repo root (paths.py -> songcoach -> repo root)
    return Path(__file__).resolve().parents[1]


def app_data_root() -> Path:
    """Base writable directory for user data + the DB cache."""
    if is_frozen():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(".")   # dev: the working directory, exactly as before


def data_dir() -> Path:
    """Where recordings/stems live — the durable source of truth."""
    return app_data_root() / "data"


def db_file() -> Path:
    return app_data_root() / "songcoach.db"


def database_url() -> str:
    if is_frozen():
        return f"sqlite:///{db_file().resolve()}"   # absolute path -> sqlite:////...
    return "sqlite:///./songcoach.db"                # unchanged in dev


def _bin(name: str) -> str:
    """A bundled helper binary when frozen, else the bare name (found on PATH)."""
    return str(resource_dir() / name) if is_frozen() else name


def ffmpeg_bin() -> str:
    return _bin("ffmpeg")


def ffprobe_bin() -> str:
    return _bin("ffprobe")


def ensure_dirs() -> None:
    """Create the writable data + DB locations before anything touches them."""
    data_dir().mkdir(parents=True, exist_ok=True)
    db_file().parent.mkdir(parents=True, exist_ok=True)
