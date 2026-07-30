import pytest

from songcoach import stem_queue
from songcoach.apple_music import session as session_mod
from songcoach.apple_music.session import AppleMusicSession
from songcoach.apple_music.watcher import MusicState
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus
from songcoach.pipeline.recorder import RecordingResult


class FakeSegRec:
    """Records the segment lifecycle; finish() returns a configurable duration."""
    duration = 30.0
    instances = []

    def __init__(self, out_dir, *, max_seconds=None):
        self.out_dir = out_dir
        self.calls = []
        FakeSegRec.instances.append(self)

    def start(self): self.calls.append("start")
    def pause(self): self.calls.append("pause")
    def resume(self): self.calls.append("resume")
    def finish(self):
        self.calls.append("finish")
        return RecordingResult(audio_path=self.out_dir / "capture.m4a",
                               duration=FakeSegRec.duration)


@pytest.fixture
def wired(monkeypatch, storage_dir):
    FakeSegRec.instances = []
    FakeSegRec.duration = 30.0
    enqueued = []
    monkeypatch.setattr(session_mod, "SegmentedRecorder", FakeSegRec)
    monkeypatch.setattr(session_mod.stem_queue, "enqueue", lambda jid: enqueued.append(jid))
    monkeypatch.setattr(session_mod.artwork, "fetch_artwork_async", lambda jid: None)
    return enqueued


def _play(tid, name="Song", artist="Artist"):
    return MusicState("playing", tid, name, artist)


def test_single_song_play_then_stop_dispatches(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    assert s.status()["phase"] == "capturing"
    s.on_state(MusicState("stopped"))
    assert wired == [_only_job_id(s)] or len(wired) == 1
    assert FakeSegRec.instances[0].calls == ["start", "finish"]
    s.stop()


def test_pause_resume_is_one_job_two_segments(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    s.on_state(MusicState("paused", "A", "Song", "Artist"))
    assert s.status()["phase"] == "paused"
    s.on_state(_play("A"))                       # same track resumes
    s.on_state(MusicState("stopped"))
    assert len(wired) == 1                         # one job dispatched
    assert FakeSegRec.instances[0].calls == ["start", "pause", "resume", "finish"]
    assert len(FakeSegRec.instances) == 1         # only one recorder → one song
    s.stop()


def test_track_change_finalizes_and_starts_next(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    s.on_state(_play("B"))                         # continuous advance
    assert len(wired) == 1                         # A dispatched
    assert len(FakeSegRec.instances) == 2         # A finished, B started
    s.on_state(MusicState("stopped"))
    assert len(wired) == 2
    s.stop()


def test_short_song_is_discarded(wired, storage_dir):
    FakeSegRec.duration = 2.0                      # below 5s
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    job_id = _only_job_id(s)
    s.on_state(MusicState("stopped"))
    assert wired == []                             # not enqueued
    assert SessionLocal().get(Job, job_id) is None  # job row deleted
    s.stop()


def test_stop_button_finalizes_current(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    s.stop()
    assert len(wired) == 1                         # current song dispatched on Stop
    assert s.status()["active"] is False


def test_mid_song_entry_captures_current(wired):
    # Mode starts while a song already plays → begins capturing immediately.
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    assert s.status()["phase"] == "capturing"
    s.stop()


def _only_job_id(session):
    # The session exposes its current job id via status()/internal for the test.
    return session._job_id
