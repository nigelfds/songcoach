import pytest

from songcoach import recording
from songcoach.pipeline.recorder import RecorderError


def test_manual_start_blocked_while_apple_music_active(storage_dir, monkeypatch):
    recording.set_apple_music_active(True)
    try:
        with pytest.raises(RecorderError):
            recording.start(title="X")
    finally:
        recording.set_apple_music_active(False)
    assert recording.apple_music_active() is False
