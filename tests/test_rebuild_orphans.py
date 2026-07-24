from sqlalchemy import select

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus
from songcoach.rebuild import rebuild


def _capture(storage_dir, job_id):
    rec = storage_dir / "recordings" / job_id
    rec.mkdir(parents=True)
    (rec / "capture.m4a").write_bytes(b"fake audio")


def test_rebuild_indexes_sidecarless_orphan(storage_dir):
    _capture(storage_dir, "abc123")
    rebuild(reset=True)
    s = SessionLocal()
    try:
        job = s.get(Job, "abc123")
        assert job is not None
        assert job.status == JobStatus.failed
        assert job.title.startswith("Untitled recording")
    finally:
        s.close()


def test_rebuild_does_not_duplicate_published_id(storage_dir):
    # A done job with a jobs/ sidecar AND a lingering capture → jobs/ wins, no dup row.
    metadata.write_meta(Job(id="abc123", title="Real Song", status=JobStatus.done))
    _capture(storage_dir, "abc123")
    rebuild(reset=True)
    s = SessionLocal()
    try:
        rows = [j for j in s.scalars(select(Job)).all() if j.id == "abc123"]
        assert len(rows) == 1
        assert rows[0].title == "Real Song"
        assert rows[0].status == JobStatus.done
    finally:
        s.close()
