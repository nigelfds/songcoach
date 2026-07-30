import subprocess
from pathlib import Path

import pytest

from songcoach.pipeline import segmented_recorder as sr
from songcoach.pipeline.recorder import RecordingResult, RecorderError


class FakeRecorder:
    """Stands in for the real syscap-backed Recorder: writes a stub segment file."""
    def __init__(self, out_dir, *, max_seconds=None):
        self.out_dir = Path(out_dir)
        self.audio_path = self.out_dir / "capture.m4a"

    def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.audio_path.write_bytes(b"seg-audio")

    def stop(self, timeout=10.0):
        return RecordingResult(audio_path=self.audio_path, duration=30.0)


@pytest.fixture
def fake_recorder(monkeypatch):
    monkeypatch.setattr(sr.recorder_mod, "Recorder", FakeRecorder)


def _fake_ffmpeg_ok(monkeypatch, dest_marker=b"concatenated"):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        # last arg is the dest path
        Path(cmd[-1]).write_bytes(dest_marker)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    return calls


def test_single_segment_skips_concat(fake_recorder, monkeypatch, tmp_path):
    calls = _fake_ffmpeg_ok(monkeypatch)
    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start()
    result = rec.finish()
    assert result.audio_path == tmp_path / "rec" / "capture.m4a"
    assert result.audio_path.exists()
    assert result.duration == 30.0
    assert calls == []                        # no ffmpeg for a single segment


def test_multi_segment_concatenates(fake_recorder, monkeypatch, tmp_path):
    calls = _fake_ffmpeg_ok(monkeypatch)
    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start()
    rec.pause()
    rec.resume()
    rec.pause()
    result = rec.finish()
    assert result.audio_path.exists()
    assert result.duration == 60.0            # two 30s segments summed
    assert len(calls) == 1                     # one concat invocation
    assert "-c" in calls[0] and "copy" in calls[0]


def test_concat_copy_failure_falls_back_to_reencode(fake_recorder, monkeypatch, tmp_path):
    attempts = []
    def fake_run(cmd, **kw):
        attempts.append(cmd)
        if "copy" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="copy failed")
        Path(cmd[-1]).write_bytes(b"reencoded")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(sr.subprocess, "run", fake_run)

    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start(); rec.pause(); rec.resume(); rec.pause()
    result = rec.finish()
    assert result.audio_path.read_bytes() == b"reencoded"
    assert len(attempts) == 2                  # copy attempt, then re-encode
    assert any("aac" in c for c in attempts[1])


def test_segment_stop_failure_is_dropped_not_fatal(monkeypatch, tmp_path):
    """A segment whose stop() raises is dropped; other segments continue."""
    class FailingSecondRecorder:
        stop_count = 0  # class-level counter shared across instances

        def __init__(self, out_dir, *, max_seconds=None):
            self.out_dir = Path(out_dir)
            self.audio_path = self.out_dir / "capture.m4a"

        def start(self):
            self.out_dir.mkdir(parents=True, exist_ok=True)
            if FailingSecondRecorder.stop_count == 0:
                self.audio_path.write_bytes(b"seg-audio")
            # second segment writes nothing

        def stop(self, timeout=10.0):
            FailingSecondRecorder.stop_count += 1
            if FailingSecondRecorder.stop_count == 2:
                raise RecorderError("syscap produced no audio file")
            return RecordingResult(audio_path=self.audio_path, duration=30.0)

    monkeypatch.setattr(sr.recorder_mod, "Recorder", FailingSecondRecorder)
    calls = _fake_ffmpeg_ok(monkeypatch)

    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start()
    rec.pause()
    rec.resume()
    result = rec.finish()
    assert result.duration == 30.0            # only the good segment counts
    assert result.audio_path.exists()
    assert rec._active is None                # recorder is not stuck
    assert calls == []                         # no ffmpeg for a single remaining segment


def test_concat_both_paths_fail_raises_recordererror(fake_recorder, monkeypatch, tmp_path):
    """When both copy and re-encode fail, RecorderError is raised."""
    def fake_run_fails(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr="nope")
    monkeypatch.setattr(sr.subprocess, "run", fake_run_fails)

    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start()
    rec.pause()
    rec.resume()
    rec.pause()
    with pytest.raises(RecorderError):
        rec.finish()
