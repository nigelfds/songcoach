import pytest
from fastapi.testclient import TestClient

from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


@pytest.fixture
def client(storage_dir, monkeypatch):
    calls = []
    from songcoach import jobs
    monkeypatch.setattr(jobs, "enqueue_processing", lambda jid: calls.append(jid))
    from songcoach.main import app
    c = TestClient(app)
    c.enqueue_calls = calls
    return c


def _failed_job(with_capture, storage_dir):
    s = SessionLocal()
    job = Job(title="T", status=JobStatus.failed, error="boom")
    s.add(job)
    s.commit()
    jid = job.id
    s.close()
    if with_capture:
        rec = storage_dir / "recordings" / jid
        rec.mkdir(parents=True)
        (rec / "capture.m4a").write_bytes(b"x")
    return jid


def test_retry_resets_and_enqueues(client, storage_dir):
    jid = _failed_job(True, storage_dir)
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    assert client.enqueue_calls == [jid]


def test_retry_409_when_capture_missing(client, storage_dir):
    jid = _failed_job(False, storage_dir)
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 409
    assert client.enqueue_calls == []


def test_serialize_exposes_resumable(client, storage_dir):
    jid = _failed_job(True, storage_dir)
    body = client.get(f"/api/jobs/{jid}").json()
    assert body["resumable"] is True


def test_retry_404_unknown_job(client):
    r = client.post("/api/jobs/doesnotexist/retry")
    assert r.status_code == 404
    assert client.enqueue_calls == []


def test_retry_409_wrong_status(client, storage_dir):
    from songcoach.db import SessionLocal
    from songcoach.models import Job, JobStatus
    s = SessionLocal()
    job = Job(title="T", status=JobStatus.done)
    s.add(job)
    s.commit()
    jid = job.id
    s.close()
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 409
    assert client.enqueue_calls == []


def test_retry_409_recording_in_progress(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    jid = _failed_job(True, storage_dir)
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 409
    assert client.enqueue_calls == []
