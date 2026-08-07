# tests/test_reprocess.py
import json

from songcoach import metadata, stem_queue
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus, Track, TrackKind
from songcoach.pipeline import process
from songcoach.pipeline.separator import SeparationResult


def _seed_done_job(storage_dir, jid="r1", legacy=True):
    """A done job with original.mp3 + legacy drums/no_drums stems + a marker."""
    s = SessionLocal()
    try:
        job = Job(id=jid, title="Song", artist="A", status=JobStatus.done, duration_seconds=100.0)
        job.tracks.append(Track(kind=TrackKind.original, storage_key=f"jobs/{jid}/original.mp3", duration_seconds=100.0))
        job.tracks.append(Track(kind=TrackKind.drums, storage_key=f"jobs/{jid}/drums.mp3", duration_seconds=100.0))
        if legacy:
            job.tracks.append(Track(kind=TrackKind.no_drums, storage_key=f"jobs/{jid}/no_drums.mp3", duration_seconds=100.0))
        s.add(job); s.commit()
        d = metadata.job_dir(jid); d.mkdir(parents=True, exist_ok=True)
        for f in ("original", "drums", "no_drums"):
            (d / f"{f}.mp3").write_bytes(b"audio")
        metadata.write_meta(s.get(Job, jid))
    finally:
        s.close()
    # give it a marker to prove preservation
    metadata.write_markers(jid, [{"id": "x", "time": 12.0, "name": "Solo"}])
    return jid


def _fake_separate(dst_root):
    """Return a separator.separate stand-in that writes fake stem files."""
    def _sep(source, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for name in ("drums", "vocals", "no_drums_no_vocals"):
            p = out_dir / f"{name}.mp3"; p.write_bytes(b"stem"); paths[name] = p
        return SeparationResult(drums_path=paths["drums"], vocals_path=paths["vocals"], backing_path=paths["no_drums_no_vocals"])
    return _sep


def test_reprocess_job_upgrades_to_four_stems(storage_dir, monkeypatch):
    jid = _seed_done_job(storage_dir)
    monkeypatch.setattr(process.separator, "separate", _fake_separate(storage_dir))
    process.reprocess_job(jid)

    s = SessionLocal()
    try:
        kinds = {t.kind for t in s.get(Job, jid).tracks}
        assert kinds == {TrackKind.original, TrackKind.drums, TrackKind.vocals, TrackKind.no_drums_no_vocals}
        assert s.get(Job, jid).status == JobStatus.done
    finally:
        s.close()
    d = metadata.job_dir(jid)
    assert not (d / "no_drums.mp3").exists()          # legacy stem removed
    assert (d / "vocals.mp3").exists() and (d / "no_drums_no_vocals.mp3").exists()
    assert (d / "original.mp3").exists()              # source untouched
    assert metadata.read_markers(jid) == [{"id": "x", "time": 12.0, "name": "Solo"}]   # preserved
    meta = json.loads(metadata.meta_path(jid).read_text())
    assert {t["kind"] for t in meta["tracks"]} == {"original", "drums", "vocals", "no_drums_no_vocals"}


def test_reprocess_job_missing_original_marks_failed(storage_dir, monkeypatch):
    s = SessionLocal()
    job = Job(id="r2", title="X", status=JobStatus.done)
    s.add(job); s.commit(); s.close()               # no sidecar / no original.mp3
    process.reprocess_job("r2")
    s = SessionLocal()
    try:
        assert s.get(Job, "r2").status == JobStatus.failed
    finally:
        s.close()


def test_enqueue_reprocess_dispatches_to_reprocess_job(monkeypatch):
    calls = []
    monkeypatch.setattr(stem_queue, "_run_job", lambda job_id, task="process": calls.append((job_id, task)))
    stem_queue.enqueue_reprocess("z1")
    stem_queue._queue.join()
    assert calls == [("z1", "reprocess")]
