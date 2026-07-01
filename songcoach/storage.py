"""Pluggable storage: local filesystem for dev, S3 for production.

Both backends expose the same interface:
    save(local_path, key) -> None
    url(key) -> str            # something a browser <audio> can load
    delete(key) -> None
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from .config import settings


class Storage(ABC):
    @abstractmethod
    def save(self, local_path: str | Path, key: str) -> None: ...

    @abstractmethod
    def url(self, key: str) -> str: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class LocalStorage(Storage):
    """Copies files under LOCAL_STORAGE_DIR; served by FastAPI at /media/{key}."""

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


class S3Storage(Storage):
    """Uploads to S3 and hands out time-limited signed URLs."""

    def __init__(self, bucket: str, region: str, ttl: int) -> None:
        import boto3  # imported lazily so local dev needn't install nothing extra

        self.bucket = bucket
        self.ttl = ttl
        self.client = boto3.client("s3", region_name=region)

    def save(self, local_path: str | Path, key: str) -> None:
        content_type = "audio/mpeg" if key.endswith(".mp3") else "application/octet-stream"
        self.client.upload_file(
            str(local_path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def url(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.ttl,
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 but S3_BUCKET is not set")
        return S3Storage(settings.s3_bucket, settings.aws_region, settings.s3_signed_url_ttl)
    return LocalStorage(settings.local_storage_dir)
