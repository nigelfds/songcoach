from songcoach import metadata
from songcoach.models import Job
from songcoach.pipeline.process import _fail


def test_fail_writes_failed_sidecar(db, storage_dir):
    job = Job(title="T")
    db.add(job)
    db.commit()
    _fail(db, job.id, "No module named 'numpy.core.multiarray'")
    meta = metadata.read_meta(metadata.meta_path(job.id))
    assert meta["status"] == "failed"
    assert "numpy" in meta["error"]
