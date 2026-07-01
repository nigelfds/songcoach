"""Local-filesystem storage for recordings and stems.

Files are copied under LOCAL_STORAGE_DIR and served by FastAPI at /media/{key}.
    save(local_path, key) -> None
    url(key) -> str            # something a browser <audio> can load
    delete(key) -> None
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import settings


class LocalStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.base_dir / key

    def save(self, local_path: str | Path, key: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

    def url(self, key: str) -> str:
        return f"/media/{key}"

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage() -> LocalStorage:
    return LocalStorage(settings.local_storage_dir)
