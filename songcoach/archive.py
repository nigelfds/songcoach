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
    job_count = sum(1 for p in (root / _TOP_DIRS[0]).glob("*") if p.is_dir())
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


def _within(target: Path, root: Path) -> bool:
    try:
        return target.resolve().is_relative_to(root.resolve())
    except (ValueError, OSError):
        return False


def import_archive(zip_path: Path) -> ImportResult:
    """Extract jobs/+recordings/ members over data/ (cp -rf), rebuild the cache."""
    from .rebuild import rebuild  # local import avoids a cycle at module load

    root = _data_root()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That doesn't look like a SongCoach export.") from exc

    archive_job_ids: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, Path]] = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if not parts or parts[0] not in _TOP_DIRS:
                continue  # whitelist: ignore manifest + anything else
            # Reject members with . or .. components (defense-in-depth on zip-slip).
            if any(p in (".", "..") for p in parts):
                log.warning("Skipping archive member with . or .. component: %s", info.filename)
                continue
            target = root / info.filename
            if not _within(target, root):
                log.warning("Skipping unsafe archive member: %s", info.filename)
                continue
            if parts[0] == "jobs" and len(parts) >= 2:
                archive_job_ids.add(parts[1])
            members.append((info, target))

        pre_existing = {j for j in archive_job_ids if (root / "jobs" / j).is_dir()}

        for info, target in members:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            except OSError as exc:
                log.warning("Failed to extract archive member (skipping): %s — %s", info.filename, exc)
                continue

    rebuild(reset=True)
    return ImportResult(added=len(archive_job_ids - pre_existing), updated=len(pre_existing))
