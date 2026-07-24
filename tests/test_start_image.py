import pytest
from fastapi.testclient import TestClient

from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


@pytest.fixture
def client(storage_dir, monkeypatch):
    calls = []
    from songcoach import fetch_thumbnails, recording

    def fake_start(**kw):
        s = SessionLocal()
        job = Job(title=kw.get("title"), artist=kw.get("artist"),
                  youtube_url=kw.get("youtube_url"), status=JobStatus.recording)
        s.add(job)
        s.commit()
        jid = job.id
        s.close()
        return jid

    monkeypatch.setattr(recording, "start", fake_start)
    monkeypatch.setattr(fetch_thumbnails, "store_image_from_url_async",
                        lambda jid, url: calls.append((jid, url)))
    from songcoach.main import app
    c = TestClient(app)
    c.image_calls = calls
    return c


def test_start_with_image_url_fires_store(client):
    r = client.post("/api/recordings/start", json={"title": "T", "image_url": "http://x/pic.jpg"})
    assert r.status_code == 201
    assert len(client.image_calls) == 1
    assert client.image_calls[0][1] == "http://x/pic.jpg"


def test_start_without_image_url_no_store(client):
    r = client.post("/api/recordings/start", json={"title": "T"})
    assert r.status_code == 201
    assert client.image_calls == []


def test_start_blank_image_url_no_store(client):
    r = client.post("/api/recordings/start", json={"title": "T", "image_url": "   "})
    assert r.status_code == 201
    assert client.image_calls == []
