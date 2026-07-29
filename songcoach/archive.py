"""Export/import the data/ library as a .zip.

data/jobs/<id>/ (stems + meta.json + thumbnail) and data/recordings/<id>/ are the
source of truth; songcoach.db is a disposable index. So an export is just a zip of
data/, and an import lays files back down and rebuilds the cache.
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .config import settings

log = logging.getLogger("songcoach.archive")

MANIFEST_NAME = "songcoach-export.json"
_TOP_DIRS = ("jobs", "recordings")


class ArchiveError(Exception):
    """The upload isn't a usable SongCoach archive."""


@dataclass
class ImportResult:
    added: int
    updated: int


def _data_root() -> Path:
    return Path(settings.local_storage_dir)


def build_export(dest_zip: Path) -> int:
    """Zip data/jobs + data/recordings + a manifest into dest_zip. Return job count."""
    root = _data_root()
    job_count = sum(1 for p in (root / "jobs").glob("*") if p.is_dir())
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_STORED) as zf:
        for top in _TOP_DIRS:
            base = root / top
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.name != ".DS_Store":
                    zf.write(path, arcname=str(path.relative_to(root)))
        manifest = {
            "app": "SongCoach",
            "schema": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "jobs": job_count,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    return job_count
