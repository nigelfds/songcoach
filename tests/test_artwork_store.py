from pathlib import Path

from songcoach import fetch_thumbnails, metadata


def test_store_image_from_file_writes_thumbnail(storage_dir, tmp_path):
    src = tmp_path / "art.jpg"
    src.write_bytes(b"\xff\xd8\xff" + b"x" * 500)   # small stub image
    ok = fetch_thumbnails.store_image_from_file("job1", src)
    assert ok is True
    dest = metadata.thumbnail_path("job1")
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()


def test_store_image_from_file_skips_missing(storage_dir, tmp_path):
    assert fetch_thumbnails.store_image_from_file("job2", tmp_path / "nope.jpg") is False
    assert not metadata.thumbnail_path("job2").exists()


def test_store_image_from_file_skips_oversized(storage_dir, tmp_path):
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * (fetch_thumbnails._MAX_IMAGE_BYTES + 1))
    assert fetch_thumbnails.store_image_from_file("job3", big) is False
    assert not metadata.thumbnail_path("job3").exists()
