import pytest
from fastapi.testclient import TestClient


class FakeWatcher:
    def __init__(self, on_state, *, interval=1.0):
        self.on_state = on_state
        self.permission_error = False
        self.started = False
    def start(self): self.started = True
    def stop(self): self.started = False


@pytest.fixture
def client(storage_dir, monkeypatch):
    from songcoach.apple_music import service
    # Don't spawn real osascript threads.
    monkeypatch.setattr(service, "MusicWatcher", FakeWatcher)
    # Reset any leftover global mode between tests.
    service._reset_for_tests()
    from songcoach.main import app
    return TestClient(app)


def test_start_status_stop_cycle(client):
    r = client.post("/api/apple-music/start")
    assert r.status_code == 200
    assert r.json()["active"] is True

    s = client.get("/api/apple-music/status").json()
    assert s["active"] is True and s["phase"] == "armed"
    assert s["captured"] == [] and "permission_error" in s

    r = client.post("/api/apple-music/stop")
    assert r.status_code == 200
    assert client.get("/api/apple-music/status").json()["active"] is False


def test_start_409_when_already_active(client):
    client.post("/api/apple-music/start")
    assert client.post("/api/apple-music/start").status_code == 409
    client.post("/api/apple-music/stop")


def test_start_409_when_manual_recording(client, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    assert client.post("/api/apple-music/start").status_code == 409
