import json

import pytest
from fastapi.testclient import TestClient

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


def _seed(jid="m1"):
    s = SessionLocal()
    try:
        job = Job(id=jid, title="Song", artist="Artist", status=JobStatus.done)
        s.add(job)
        s.commit()
        metadata.write_meta(job)   # creates the sidecar
    finally:
        s.close()
    return jid


def _sidecar(jid):
    return json.loads(metadata.meta_path(jid).read_text(encoding="utf-8"))


# --- metadata helpers ------------------------------------------------------

def test_read_markers_empty_when_none(storage_dir):
    _seed("a")
    assert metadata.read_markers("a") == []


def test_write_then_read_markers(storage_dir):
    _seed("b")
    ms = [{"id": "x", "time": 12.5, "name": "Solo"}]
    assert metadata.write_markers("b", ms) is True
    assert metadata.read_markers("b") == ms
    assert _sidecar("b")["title"] == "Song"   # other keys intact


def test_write_markers_missing_sidecar(storage_dir):
    assert metadata.write_markers("ghost", []) is False


def test_write_meta_preserves_markers(storage_dir):
    jid = _seed("c")
    metadata.write_markers(jid, [{"id": "x", "time": 1.0, "name": "A"}])
    s = SessionLocal()
    try:
        metadata.write_meta(s.get(Job, jid))   # a later edit must not wipe markers
    finally:
        s.close()
    assert metadata.read_markers(jid) == [{"id": "x", "time": 1.0, "name": "A"}]


# --- endpoints -------------------------------------------------------------

@pytest.fixture
def client(storage_dir):
    from songcoach.main import app
    return TestClient(app)


def test_get_markers(client, storage_dir):
    _seed("g")
    r = client.get("/api/jobs/g/markers")
    assert r.status_code == 200 and r.json() == {"markers": []}


def test_get_markers_404_no_sidecar(client, storage_dir):
    assert client.get("/api/jobs/nope/markers").status_code == 404


def test_put_markers_ok(client, storage_dir):
    _seed("p")
    body = {"markers": [{"id": "1", "time": 30.2, "name": "  Fill  "}]}
    r = client.put("/api/jobs/p/markers", json=body)
    assert r.status_code == 200
    assert r.json()["markers"][0]["name"] == "Fill"          # trimmed
    assert metadata.read_markers("p")[0]["time"] == 30.2      # persisted


def test_put_markers_404_no_sidecar(client, storage_dir):
    r = client.put("/api/jobs/nope/markers", json={"markers": []})
    assert r.status_code == 404


def test_put_markers_422_bad_payload(client, storage_dir):
    _seed("q")
    assert client.put("/api/jobs/q/markers", json={"markers": "nope"}).status_code == 422
    assert client.put("/api/jobs/q/markers",
                      json={"markers": [{"id": "1", "time": -5, "name": "x"}]}).status_code == 422
