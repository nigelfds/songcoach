from songcoach import reprocess as cli
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus, Track, TrackKind


def _add(jid, status=JobStatus.done, has_vocals=False):
    s = SessionLocal()
    try:
        job = Job(id=jid, title=jid, status=status)
        job.tracks.append(Track(kind=TrackKind.original, storage_key=f"jobs/{jid}/original.mp3"))
        if has_vocals:
            job.tracks.append(Track(kind=TrackKind.vocals, storage_key=f"jobs/{jid}/vocals.mp3"))
        s.add(job); s.commit()
    finally:
        s.close()


def test_cli_reprocesses_done_jobs_without_vocals(storage_dir, db, monkeypatch):
    monkeypatch.setattr(cli, "rebuild", lambda **k: 0)     # keep the seeded rows
    _add("old1", has_vocals=False)
    _add("new1", has_vocals=True)                          # already reprocessed → skip
    _add("q1", status=JobStatus.queued)                    # not done → ignore
    done_ids = []
    monkeypatch.setattr(cli, "reprocess_job", lambda jid: done_ids.append(jid))
    done, skipped, failed = cli.run(force=False)
    assert done_ids == ["old1"]
    assert (done, skipped, failed) == (1, 1, 0)


def test_cli_force_reprocesses_all_done(storage_dir, db, monkeypatch):
    monkeypatch.setattr(cli, "rebuild", lambda **k: 0)
    _add("old1", has_vocals=False)
    _add("new1", has_vocals=True)
    done_ids = []
    monkeypatch.setattr(cli, "reprocess_job", lambda jid: done_ids.append(jid))
    done, skipped, failed = cli.run(force=True)
    assert set(done_ids) == {"old1", "new1"} and (done, skipped, failed) == (2, 0, 0)
