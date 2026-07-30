import json

import pytest
from fastapi.testclient import TestClient

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus
from songcoach.rebuild import rebuild


def _seed_job(jid="j1", status=JobStatus.done):
    """A job row + its sidecar + a stem file on disk (in the tmp storage_dir)."""
    s = SessionLocal()
    try:
        job = Job(id=jid, title="Song", artist="Artist", status=status)
        s.add(job)
        s.commit()
        d = metadata.job_dir(jid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "drums.mp3").write_bytes(b"audio")
        metadata.write_meta(job)
    finally:
        s.close()
    return jid


def _sidecar(jid):
    return json.loads(metadata.meta_path(jid).read_text(encoding="utf-8"))


# --- metadata.mark_deleted -------------------------------------------------

def test_mark_deleted_sets_flag_keeps_other_keys(storage_dir):
    jid = _seed_job()
    assert metadata.mark_deleted(jid) is True
    data = _sidecar(jid)
    assert data["deleted"] is True
    assert data["title"] == "Song"      # other keys intact


def test_mark_deleted_missing_sidecar(storage_dir):
    assert metadata.mark_deleted("ghost") is False


# --- rebuild skips deleted -------------------------------------------------

def test_rebuild_skips_deleted(storage_dir):
    _seed_job("keep")
    _seed_job("gone")
    metadata.mark_deleted("gone")
    rebuild(reset=True)
    s = SessionLocal()
    try:
        assert s.get(Job, "keep") is not None
        assert s.get(Job, "gone") is None
    finally:
        s.close()


# --- DELETE endpoint -------------------------------------------------------

@pytest.fixture
def client(storage_dir):
    from songcoach.main import app
    return TestClient(app)


def test_delete_done_job_soft(client, storage_dir):
    jid = _seed_job("d1")
    r = client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 204
    s = SessionLocal()
    try:
        assert s.get(Job, jid) is None          # gone from the index
    finally:
        s.close()
    assert _sidecar(jid)["deleted"] is True      # sidecar flagged
    assert (metadata.job_dir(jid) / "drums.mp3").exists()   # files untouched


def test_delete_unknown_404(client, storage_dir):
    assert client.delete("/api/jobs/nope").status_code == 404


def test_delete_while_processing_409(client, storage_dir):
    jid = _seed_job("p1", status=JobStatus.separating)
    r = client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 409
    assert "deleted" not in _sidecar(jid)        # sidecar NOT modified


def test_deleted_job_player_page_404(client, storage_dir):
    jid = _seed_job("pl1")
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert client.get(f"/jobs/{jid}").status_code == 404


def test_delete_failed_job_not_resurrected(client, storage_dir):
    # A failed job keeps its capture on disk (that's how retry works); deleting it
    # must NOT be re-added by rebuild()'s orphan-capture scan.
    jid = _seed_job("f1", status=JobStatus.failed)
    cap_dir = storage_dir / "recordings" / jid
    cap_dir.mkdir(parents=True)
    (cap_dir / "capture.m4a").write_bytes(b"x")
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    s = SessionLocal()
    try:
        assert s.get(Job, jid) is None      # not resurrected
    finally:
        s.close()
    assert _sidecar(jid)["deleted"] is True
    assert (cap_dir / "capture.m4a").exists()   # files still untouched (soft delete)
