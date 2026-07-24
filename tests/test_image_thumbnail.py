from songcoach import fetch_thumbnails as ft
from songcoach.metadata import thumbnail_path


class _FakeHeaders:
    def __init__(self, ct):
        self._ct = ct

    def get_content_type(self):
        return self._ct


class _FakeResp:
    def __init__(self, data=b"", status=200, content_type="image/jpeg"):
        self._data = data
        self.status = status
        self.headers = _FakeHeaders(content_type)

    def read(self, n=-1):
        return self._data if (n is None or n < 0) else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_image_rejects_non_image(monkeypatch):
    monkeypatch.setattr(ft, "urlopen", lambda *a, **k: _FakeResp(b"<html>", content_type="text/html"))
    assert ft._download_image("http://x/y") is None


def test_download_image_rejects_oversize(monkeypatch):
    monkeypatch.setattr(ft, "urlopen", lambda *a, **k: _FakeResp(b"x" * 50, content_type="image/png"))
    assert ft._download_image("http://x/y", max_bytes=10) is None


def test_download_image_returns_bytes(monkeypatch):
    monkeypatch.setattr(ft, "urlopen", lambda *a, **k: _FakeResp(b"IMG", content_type="image/jpeg"))
    assert ft._download_image("http://x/y") == b"IMG"


def test_store_image_writes_thumbnail(monkeypatch, storage_dir):
    monkeypatch.setattr(ft, "_download_image", lambda url, **k: b"IMG")
    ft.store_image_from_url("job42", "http://x/y")
    p = thumbnail_path("job42")
    assert p.exists() and p.read_bytes() == b"IMG"


def test_store_image_noop_when_download_fails(monkeypatch, storage_dir):
    monkeypatch.setattr(ft, "_download_image", lambda url, **k: None)
    ft.store_image_from_url("job43", "http://x/y")
    assert not thumbnail_path("job43").exists()
