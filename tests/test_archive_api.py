import io
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(storage_dir, monkeypatch):
    from songcoach.main import app
    return TestClient(app)


def _job(storage_dir, jid):
    d = storage_dir / "jobs" / jid
    d.mkdir(parents=True)
    (d / "meta.json").write_text('{"id":"%s","status":"done"}' % jid)
    (d / "original.mp3").write_bytes(b"a")


def test_export_returns_zip(client, storage_dir):
    _job(storage_dir, "j1")
    r = client.get("/api/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "jobs/j1/meta.json" in zf.namelist()


def test_export_409_while_recording(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    assert client.get("/api/export").status_code == 409


def test_import_merges_and_returns_counts(client, storage_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("jobs/j9/meta.json", '{"id":"j9","status":"done"}')
    buf.seek(0)
    r = client.post("/api/import", files={"file": ("x.zip", buf, "application/zip")})
    assert r.status_code == 200
    assert r.json() == {"added": 1, "updated": 0}
    assert (storage_dir / "jobs" / "j9" / "meta.json").exists()


def test_import_409_while_recording(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("jobs/j/meta.json", "{}")
    buf.seek(0)
    r = client.post("/api/import", files={"file": ("x.zip", buf, "application/zip")})
    assert r.status_code == 409


def test_import_422_on_bad_archive(client, storage_dir):
    r = client.post("/api/import", files={"file": ("x.zip", io.BytesIO(b"nope"), "application/zip")})
    assert r.status_code == 422
