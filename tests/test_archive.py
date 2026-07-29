import io
import json
import zipfile
from pathlib import Path

from songcoach import archive


def _job(storage_dir: Path, jid: str):
    d = storage_dir / "jobs" / jid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": jid, "title": "T", "status": "done"}))
    (d / "original.mp3").write_bytes(b"audio")


def test_build_export_zips_jobs_and_manifest(storage_dir, tmp_path):
    _job(storage_dir, "job1")
    (storage_dir / "recordings" / "rec1").mkdir(parents=True)
    (storage_dir / "recordings" / "rec1" / "capture.m4a").write_bytes(b"cap")

    dest = tmp_path / "out.zip"
    n = archive.build_export(dest)

    assert n == 1
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "jobs/job1/meta.json" in names
        assert "jobs/job1/original.mp3" in names
        assert "recordings/rec1/capture.m4a" in names
        assert archive.MANIFEST_NAME in names
        manifest = json.loads(zf.read(archive.MANIFEST_NAME))
        assert manifest["app"] == "SongCoach"
        assert manifest["jobs"] == 1
