import pytest
from fastapi.testclient import TestClient

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


@pytest.fixture
def client(storage_dir, db, monkeypatch):
    calls = []
    from songcoach import stem_queue
    monkeypatch.setattr(stem_queue, "enqueue_reprocess", lambda jid: calls.append(jid))
    from songcoach.main import app
    c = TestClient(app)
    c.enqueue_calls = calls
    return c


def _done_with_original(jid="a"):
    s = SessionLocal()
    job = Job(id=jid, title="T", status=JobStatus.done)
    s.add(job); s.commit(); s.close()
    d = metadata.job_dir(jid); d.mkdir(parents=True, exist_ok=True)
    (d / "original.mp3").write_bytes(b"x")
    return jid


def test_reprocess_enqueues(client, storage_dir):
    jid = _done_with_original()
    r = client.post(f"/api/jobs/{jid}/reprocess")
    assert r.status_code == 200 and r.json()["status"] == "separating"
    assert client.enqueue_calls == [jid]


def test_reprocess_404_unknown(client):
    assert client.post("/api/jobs/nope/reprocess").status_code == 404
    assert client.enqueue_calls == []


def test_reprocess_409_not_done(client, storage_dir):
    s = SessionLocal(); s.add(Job(id="b", title="T", status=JobStatus.queued)); s.commit(); s.close()
    assert client.post("/api/jobs/b/reprocess").status_code == 409
    assert client.enqueue_calls == []


def test_reprocess_409_missing_original(client, storage_dir):
    s = SessionLocal(); s.add(Job(id="c", title="T", status=JobStatus.done)); s.commit(); s.close()
    assert client.post("/api/jobs/c/reprocess").status_code == 409     # no original.mp3
    assert client.enqueue_calls == []


def test_reprocess_409_recording(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    jid = _done_with_original("d")
    assert client.post(f"/api/jobs/{jid}/reprocess").status_code == 409
    assert client.enqueue_calls == []
