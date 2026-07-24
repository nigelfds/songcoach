from songcoach import metadata
from songcoach.models import Job, JobStatus


def test_to_dict_includes_error(storage_dir):
    job = Job(id="j1", title="T", status=JobStatus.failed, error="boom")
    assert metadata.to_dict(job)["error"] == "boom"


def test_write_read_roundtrips_error_and_status(storage_dir):
    job = Job(id="j1", title="T", status=JobStatus.failed, error="boom")
    loaded = metadata.read_meta(metadata.write_meta(job))
    assert loaded["error"] == "boom"
    assert loaded["status"] == "failed"
