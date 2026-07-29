import io
import json
import zipfile
from pathlib import Path

import pytest

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


def test_import_round_trip_into_empty_dir(storage_dir, tmp_path, monkeypatch):
    # Export from a populated dir...
    _job(storage_dir, "job1")
    dest = tmp_path / "out.zip"
    archive.build_export(dest)

    # ...then import into a fresh empty data dir.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(archive.settings, "local_storage_dir", empty)

    result = archive.import_archive(dest)

    assert result.added == 1
    assert result.updated == 0
    assert (empty / "jobs" / "job1" / "meta.json").exists()
    assert (empty / "jobs" / "job1" / "original.mp3").read_bytes() == b"audio"


def test_import_cp_rf_overwrites_conflict_keeps_others(storage_dir, tmp_path):
    # Local library has job1 (with an extra stem) and an unrelated job2.
    _job(storage_dir, "job1")
    (storage_dir / "jobs" / "job1" / "drums.mp3").write_bytes(b"local-drums")
    _job(storage_dir, "job2")

    # Archive re-exports job1 only, with a different original.mp3.
    src = tmp_path / "src"
    (src / "jobs" / "job1").mkdir(parents=True)
    (src / "jobs" / "job1" / "meta.json").write_text('{"id":"job1","status":"done"}')
    (src / "jobs" / "job1" / "original.mp3").write_bytes(b"archive-audio")
    zpath = tmp_path / "in.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src / "jobs" / "job1" / "meta.json", "jobs/job1/meta.json")
        zf.write(src / "jobs" / "job1" / "original.mp3", "jobs/job1/original.mp3")

    result = archive.import_archive(zpath)

    assert result.added == 0
    assert result.updated == 1
    # Conflict file overwritten...
    assert (storage_dir / "jobs" / "job1" / "original.mp3").read_bytes() == b"archive-audio"
    # ...local-only extra stem kept (cp -rf never deletes)...
    assert (storage_dir / "jobs" / "job1" / "drums.mp3").read_bytes() == b"local-drums"
    # ...unrelated job untouched.
    assert (storage_dir / "jobs" / "job2" / "meta.json").exists()


def test_import_rejects_zip_slip(storage_dir, tmp_path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("jobs/../../evil.txt", "pwned")
        zf.writestr("/etc/passwd-ish", "pwned")
    archive.import_archive(zpath)
    assert not (tmp_path / "evil.txt").exists()
    assert not (storage_dir.parent / "evil.txt").exists()


def test_import_ignores_non_whitelisted_members(storage_dir, tmp_path):
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("secrets/creds.txt", "nope")
        zf.writestr("jobs/j/meta.json", '{"id":"j","status":"done"}')
    archive.import_archive(zpath)
    assert not (storage_dir / "secrets").exists()
    assert (storage_dir / "jobs" / "j" / "meta.json").exists()


def test_import_manifestless_zip(storage_dir, tmp_path):
    zpath = tmp_path / "plain.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("jobs/j/meta.json", '{"id":"j","status":"done"}')
    result = archive.import_archive(zpath)
    assert result.added == 1


def test_import_non_zip_raises(storage_dir, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(archive.ArchiveError):
        archive.import_archive(bad)


def test_import_rejects_dotdot_component_in_job_path(storage_dir, tmp_path):
    # Build a zip with a traversal member that passes _within (resolves inside root)
    # but contains .. in the path components.
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("jobs/../recordings/evil/capture.m4a", "pwned")

    result = archive.import_archive(zpath)

    # Should be rejected: no files extracted, no jobs counted
    assert result.added == 0
    assert result.updated == 0
    # Verify the file was NOT written anywhere
    assert not (storage_dir / "recordings" / "evil").exists()
    assert not (storage_dir / "jobs" / ".." / "recordings").exists()


def test_import_survives_file_dir_collision(storage_dir, tmp_path):
    # Create a real job directory on disk.
    _job(storage_dir, "col1")

    # Build a zip with a file member named exactly "jobs/col1" (collides with dir).
    zpath = tmp_path / "collision.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("jobs/col1", b"file-content")

    # Should NOT raise; should skip the bad member and complete import.
    result = archive.import_archive(zpath)

    # Pre-existing job was not touched (added=0, updated=1 because col1 exists)
    assert result.updated == 1
    # The pre-existing job files should still exist.
    assert (storage_dir / "jobs" / "col1" / "meta.json").exists()
