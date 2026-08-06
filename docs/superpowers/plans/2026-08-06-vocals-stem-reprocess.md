# Vocals Stem + Library Reprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate every song into four rows (full/drums/**vocals**/**backing**), and let existing recordings be brought up to the new model via a CLI (whole library) and a per-song reprocess button (both call one shared core).

**Architecture:** `htdemucs` already yields drums/bass/other/vocals; `separate()` now emits drums, vocals, and backing (`bass+other`). New `TrackKind` values (`vocals`, `no_drums_no_vocals`) with the legacy `no_drums` kept for un-reprocessed songs. `reprocess_job(job_id)` re-separates from the retained `original.mp3`. The serial stem queue gains a reprocess task; a `POST /reprocess` endpoint (queued) and a `python -m songcoach.reprocess` CLI (synchronous) both use it. The player renders whatever kinds a job has and gains a reprocess button.

**Tech Stack:** Python 3.11, torch/Demucs, FastAPI, SQLAlchemy, pytest, WaveSurfer.js. Demucs is mocked in tests (never a real separation).

## Global Constraints

- **Run tests with** `.venv/bin/python -m pytest` (NOT `.venv/bin/pytest`).
- **No real Demucs in tests** — always monkeypatch `separator.separate` / `_load_model` / `apply_model` / `save_audio`.
- **4th-stem stored value is `no_drums_no_vocals`**; player label **BACKING**. Legacy `no_drums` kind is kept (old sidecars still render).
- **Reprocess source is `jobs/<id>/original.mp3`** (the raw capture is gone); `original.mp3` is never regenerated.
- **Reprocess preserves markers** — it ends with `metadata.write_meta(job)`, which already preserves `markers`/`deleted`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Separator — drums / vocals / backing

**Files:**
- Modify: `songcoach/pipeline/separator.py`
- Test: `tests/test_separator.py`

**Interfaces:**
- Produces: `SeparationResult{drums_path, vocals_path, backing_path}`; `separate(audio_path, out_dir) -> SeparationResult` writing `drums.mp3`, `vocals.mp3`, `no_drums_no_vocals.mp3`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_separator.py
import torch

from songcoach.pipeline import separator


class FakeModel:
    sources = ["drums", "bass", "other", "vocals"]
    samplerate = 44100
    audio_channels = 2


def test_separate_extracts_drums_vocals_backing(monkeypatch, tmp_path):
    monkeypatch.setattr(separator, "_load_model", lambda: FakeModel())

    # A standardized wav so separate()'s normalize/de-normalize is an identity
    # (ref.mean()~0, ref.std()~1), letting us assert on the raw source tensors.
    v = torch.linspace(-1, 1, 200)
    v = (v - v.mean()) / v.std()
    wav = torch.stack([v, v])                       # (channels=2, samples=200)

    class FakeAF:
        def __init__(self, p): pass
        def read(self, **kw): return wav
    monkeypatch.setattr(separator, "AudioFile", FakeAF)

    drums = torch.full((2, 200), 1.0)
    bass = torch.full((2, 200), 2.0)
    other = torch.full((2, 200), 3.0)
    vocals = torch.full((2, 200), 4.0)
    stacked = torch.stack([drums, bass, other, vocals])          # order == FakeModel.sources
    monkeypatch.setattr(separator, "apply_model", lambda *a, **k: stacked[None])

    saved = {}
    def fake_save(tensor, path, **kw):
        from pathlib import Path
        saved[Path(path).name] = tensor
        Path(path).write_bytes(b"x")
    monkeypatch.setattr(separator, "save_audio", fake_save)

    res = separator.separate(tmp_path / "in.mp3", tmp_path / "out")

    assert res.drums_path.name == "drums.mp3"
    assert res.vocals_path.name == "vocals.mp3"
    assert res.backing_path.name == "no_drums_no_vocals.mp3"
    assert torch.allclose(saved["drums.mp3"], drums)
    assert torch.allclose(saved["vocals.mp3"], vocals)
    assert torch.allclose(saved["no_drums_no_vocals.mp3"], bass + other)   # backing = bass+other
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_separator.py -v`
Expected: FAIL — `SeparationResult` has no `vocals_path`/`backing_path`; `no_drums_no_vocals.mp3` not produced.

- [ ] **Step 3: Implement**

In `songcoach/pipeline/separator.py`, replace the `SeparationResult` dataclass and
the two-stem tail of `separate()`:

```python
@dataclass
class SeparationResult:
    drums_path: Path
    vocals_path: Path
    backing_path: Path
```

and, in `separate()`, replace everything from `# Two stems:` through the `return`:

```python
    # Three stems: keep `drums` and `vocals`; sum the rest (bass+other) into backing.
    by_name = dict(zip(model.sources, sources))
    for required in ("drums", "vocals"):
        if required not in by_name:
            raise RuntimeError(f"model {settings.demucs_model} has no '{required}' source")
    drums = by_name["drums"]
    vocals = by_name["vocals"]
    backing = torch.zeros_like(drums)
    for name, tensor in by_name.items():
        if name not in ("drums", "vocals"):
            backing = backing + tensor

    drums_path = out_dir / "drums.mp3"
    vocals_path = out_dir / "vocals.mp3"
    backing_path = out_dir / "no_drums_no_vocals.mp3"
    save_audio(drums, str(drums_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)
    save_audio(vocals, str(vocals_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)
    save_audio(backing, str(backing_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)

    if not (drums_path.exists() and vocals_path.exists() and backing_path.exists()):
        raise RuntimeError("Demucs produced no stem files")
    return SeparationResult(drums_path=drums_path, vocals_path=vocals_path, backing_path=backing_path)
```

Update the module docstring's "drums / no-drums" line to "drums / vocals / backing".

- [ ] **Step 4: Run test + full suite**

Run: `.venv/bin/python -m pytest tests/test_separator.py -v` → PASS.
Run: `.venv/bin/python -m pytest -q` → NOTE: `tests/` may now have a failure in a process test if one referenced `no_drums_path`; if so it's fixed in Task 2. Confirm `test_separator.py` passes; other pre-existing tests that don't touch separator stay green.

- [ ] **Step 5: Commit**

```bash
git add songcoach/pipeline/separator.py tests/test_separator.py
git commit -m "feat(separator): emit drums/vocals/backing stems"
```

---

### Task 2: Track model, publish, reprocess core, queue dispatch

**Files:**
- Modify: `songcoach/models.py` (TrackKind), `songcoach/pipeline/process.py` (shared publish + new deliverables + `reprocess_job`), `songcoach/stem_queue.py` (reprocess task), `tests/test_stem_queue.py` (fake signature)
- Test: `tests/test_reprocess.py`

**Interfaces:**
- Consumes: `separator.separate` (Task 1), `metadata`, `storage`, `SessionLocal`, `Job`, `Track`, `TrackKind`.
- Produces: `TrackKind.vocals`, `TrackKind.no_drums_no_vocals`; `process._publish_stems(job, storage, {kind: path})`; `process.reprocess_job(job_id)`; `stem_queue.enqueue_reprocess(job_id)`.

- [ ] **Step 1: Extend `TrackKind`**

In `songcoach/models.py`, change the enum to (keep `no_drums` last, add two):

```python
class TrackKind(str, enum.Enum):
    original = "original"                       # the full song
    drums = "drums"                             # drums only
    vocals = "vocals"                           # vocals only
    no_drums_no_vocals = "no_drums_no_vocals"   # backing: bass + other
    no_drums = "no_drums"                       # legacy: pre-reprocess play-along
```

- [ ] **Step 2: Refactor publish + add `reprocess_job` in `process.py`**

Add a shared publish helper (near `_to_mp3`):

```python
def _publish_stems(job: Job, storage, deliverables: dict) -> None:
    """storage.save each {TrackKind: path} into jobs/<id>/<kind>.mp3 and append a Track.

    Does NOT clear job.tracks — the caller clears first, then publishes.
    """
    for kind, path in deliverables.items():
        key = f"jobs/{job.id}/{kind.value}.mp3"
        storage.save(path, key)
        job.tracks.append(
            Track(kind=kind, storage_key=key, duration_seconds=job.duration_seconds)
        )
```

In `process_capture`, replace the deliverables block — i.e. the existing
`deliverables = { TrackKind.original: …, TrackKind.drums: …, TrackKind.no_drums: … }`
dict, the `job.tracks.clear()`, and the `for kind, path in deliverables.items(): …`
append loop — with this:

```python
            job.tracks.clear()
            _publish_stems(job, storage, {
                TrackKind.original: original_mp3,
                TrackKind.drums: sep.drums_path,
                TrackKind.vocals: sep.vocals_path,
                TrackKind.no_drums_no_vocals: sep.backing_path,
            })
```

(`sep` is a `SeparationResult` from Task 1 with `.drums_path`/`.vocals_path`/
`.backing_path` — the old `sep.no_drums_path` no longer exists.)

Add `reprocess_job` at the end of the module (uses the existing `_set`, `_fail`,
`_publish_stems`, `separator`, `metadata`, `get_storage`, `SessionLocal`,
`JobStatus`, `TrackKind`, `Track`; add `from pathlib import Path` — already
imported; `tempfile` — already imported):

```python
def reprocess_job(job_id: str) -> None:
    """Re-separate a finished job from its retained original.mp3 into the current
    stem set (drums/vocals/backing), replacing the legacy no_drums stem. The
    original track + markers are preserved."""
    session = SessionLocal()
    storage = get_storage()
    pub_dir = metadata.job_dir(job_id)
    original = pub_dir / "original.mp3"
    try:
        job = session.get(Job, job_id)
        if job is None:
            log.error("Reprocess: job %s not found", job_id)
            return
        if not original.exists():
            raise RuntimeError(f"original audio missing at {original}")

        _set(session, job, status=JobStatus.separating, progress=40)
        with tempfile.TemporaryDirectory(prefix="songcoach-re-") as tmp:
            sep = separator.separate(original, Path(tmp) / "separated")
            _set(session, job, status=JobStatus.uploading, progress=80)
            job.tracks.clear()
            job.tracks.append(Track(
                kind=TrackKind.original,
                storage_key=f"jobs/{job_id}/original.mp3",
                duration_seconds=job.duration_seconds,
            ))
            _publish_stems(job, storage, {
                TrackKind.drums: sep.drums_path,
                TrackKind.vocals: sep.vocals_path,
                TrackKind.no_drums_no_vocals: sep.backing_path,
            })
            (pub_dir / "no_drums.mp3").unlink(missing_ok=True)   # drop legacy stem
            _set(session, job, status=JobStatus.done, progress=100, error=None)
            metadata.write_meta(job)   # preserves markers/deleted
            log.info("Reprocessed %s: %s", job_id, job.title)
    except subprocess.CalledProcessError as exc:
        _fail(session, job_id, (exc.stderr or str(exc)).strip()[-500:])
    except Exception as exc:  # noqa: BLE001
        log.exception("Reprocess %s failed", job_id)
        _fail(session, job_id, str(exc))
    finally:
        session.close()
```

- [ ] **Step 3: Add reprocess dispatch to the queue**

In `songcoach/stem_queue.py`, change the queue to carry a task and add
`enqueue_reprocess`:

```python
_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()   # (job_id, task)
```

```python
def _run_job(job_id: str, task: str = "process") -> None:
    # Imported lazily so importing this module doesn't pull in the pipeline.
    from .pipeline.process import process_capture, reprocess_job
    if task == "reprocess":
        reprocess_job(job_id)
    else:
        process_capture(job_id)
```

```python
def _worker_loop() -> None:
    while True:
        job_id, task = _queue.get()
        try:
            _run_job(job_id, task)
        except Exception:  # noqa: BLE001
            log.exception("Stem worker failed for %s", job_id)
        finally:
            _queue.task_done()
```

```python
def enqueue(job_id: str) -> None:
    """Queue a captured job for separation. Returns immediately."""
    _ensure_worker()
    _queue.put((job_id, "process"))
    log.info("Enqueued %s for separation (queue depth ~%d)", job_id, _queue.qsize())


def enqueue_reprocess(job_id: str) -> None:
    """Queue a finished job to be re-separated into the current stem set."""
    _ensure_worker()
    _queue.put((job_id, "reprocess"))
    log.info("Enqueued %s for reprocess (queue depth ~%d)", job_id, _queue.qsize())
```

**Update `tests/test_stem_queue.py`** — the two fakes it monkeypatches onto
`_run_job` must accept the new signature. Change `def fake_run(job_id):` to
`def fake_run(job_id, task="process"):` in BOTH `test_enqueue_runs_serially_in_fifo_order`
and `test_worker_survives_a_failing_job` (the assertions on order/ids are
unchanged).

- [ ] **Step 4: Write the reprocess tests**

```python
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
```

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_reprocess.py tests/test_stem_queue.py -v` → PASS.
Run: `.venv/bin/python -m pytest -q` → whole suite green (Task 1's separator test + these).

- [ ] **Step 6: Commit**

```bash
git add songcoach/models.py songcoach/pipeline/process.py songcoach/stem_queue.py tests/test_stem_queue.py tests/test_reprocess.py
git commit -m "feat(reprocess): 4-stem publish + reprocess_job + queue dispatch"
```

---

### Task 3: Reprocess endpoint + CLI

**Files:**
- Modify: `songcoach/routes/api.py` (`POST /jobs/{id}/reprocess`)
- Create: `songcoach/reprocess.py` (CLI)
- Test: `tests/test_reprocess_api.py`, `tests/test_reprocess_cli.py`

**Interfaces:**
- Consumes: `stem_queue.enqueue_reprocess`, `process.reprocess_job`, `metadata.job_dir`, `rebuild`.
- Produces: `POST /api/jobs/{id}/reprocess`; `songcoach.reprocess.run(force: bool) -> tuple[int,int,int]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reprocess_api.py
import pytest
from fastapi.testclient import TestClient

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


@pytest.fixture
def client(storage_dir, monkeypatch):
    calls = []
    from songcoach import stem_queue
    monkeypatch.setattr(stem_queue, "enqueue_reprocess", lambda jid: calls.append(jid))
    from songcoach.main import app
    c = TestClient(app)
    c.enqueue_calls = calls
    return c


def _done_with_original(jid="a"):
    s = SessionLocal()
    job = Job(id=jid, title="T", status=JobStatus.done)
    s.add(job); s.commit(); s.close()
    d = metadata.job_dir(jid); d.mkdir(parents=True, exist_ok=True)
    (d / "original.mp3").write_bytes(b"x")
    return jid


def test_reprocess_enqueues(client, storage_dir):
    jid = _done_with_original()
    r = client.post(f"/api/jobs/{jid}/reprocess")
    assert r.status_code == 200 and r.json()["status"] == "separating"
    assert client.enqueue_calls == [jid]


def test_reprocess_404_unknown(client):
    assert client.post("/api/jobs/nope/reprocess").status_code == 404
    assert client.enqueue_calls == []


def test_reprocess_409_not_done(client, storage_dir):
    s = SessionLocal(); s.add(Job(id="b", title="T", status=JobStatus.queued)); s.commit(); s.close()
    assert client.post("/api/jobs/b/reprocess").status_code == 409
    assert client.enqueue_calls == []


def test_reprocess_409_missing_original(client, storage_dir):
    s = SessionLocal(); s.add(Job(id="c", title="T", status=JobStatus.done)); s.commit(); s.close()
    assert client.post("/api/jobs/c/reprocess").status_code == 409     # no original.mp3


def test_reprocess_409_recording(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    jid = _done_with_original("d")
    assert client.post(f"/api/jobs/{jid}/reprocess").status_code == 409
```

```python
# tests/test_reprocess_cli.py
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


def test_cli_reprocesses_done_jobs_without_vocals(storage_dir, monkeypatch):
    monkeypatch.setattr(cli, "rebuild", lambda **k: 0)     # keep the seeded rows
    _add("old1", has_vocals=False)
    _add("new1", has_vocals=True)                          # already reprocessed → skip
    _add("q1", status=JobStatus.queued)                    # not done → ignore
    done_ids = []
    monkeypatch.setattr(cli, "reprocess_job", lambda jid: done_ids.append(jid))
    done, skipped, failed = cli.run(force=False)
    assert done_ids == ["old1"]
    assert (done, skipped, failed) == (1, 1, 0)


def test_cli_force_reprocesses_all_done(storage_dir, monkeypatch):
    monkeypatch.setattr(cli, "rebuild", lambda **k: 0)
    _add("old1", has_vocals=False)
    _add("new1", has_vocals=True)
    done_ids = []
    monkeypatch.setattr(cli, "reprocess_job", lambda jid: done_ids.append(jid))
    done, skipped, failed = cli.run(force=True)
    assert set(done_ids) == {"old1", "new1"} and (done, skipped, failed) == (2, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_reprocess_api.py tests/test_reprocess_cli.py -v`
Expected: FAIL — route absent / `No module named 'songcoach.reprocess'`.

- [ ] **Step 3: Add the endpoint**

In `songcoach/routes/api.py`, add `stem_queue` to the package import line
(`from .. import ... , stem_queue, ...`). Add:

```python
@router.post("/jobs/{job_id}/reprocess", response_model=JobOut)
def reprocess_job_endpoint(job_id: str, session: Session = Depends(get_session)):
    """Re-separate a finished job into the current stem set (adds the vocals stem)."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    if job.status != JobStatus.done:
        raise HTTPException(status_code=409, detail="Only finished recordings can be reprocessed.")
    if not (metadata.job_dir(job_id) / "original.mp3").exists():
        raise HTTPException(status_code=409, detail="Nothing to reprocess — the source audio is missing.")
    job.status = JobStatus.separating
    job.progress = 10
    session.commit()
    stem_queue.enqueue_reprocess(job_id)
    return _serialize(job)
```

- [ ] **Step 4: Add the CLI**

Create `songcoach/reprocess.py`:

```python
"""Reprocess the library into the current stem set (adds a vocals stem).

    python -m songcoach.reprocess           # every done job missing vocals
    python -m songcoach.reprocess --force    # even ones already reprocessed

Runs one song at a time (in this process), so it doesn't need the web server.
"""
from __future__ import annotations

import argparse
import logging

from .db import SessionLocal
from .models import Job, JobStatus, TrackKind
from .pipeline.process import reprocess_job
from .rebuild import rebuild

log = logging.getLogger("songcoach.reprocess")


def _has_vocals(job: Job) -> bool:
    return any(t.kind == TrackKind.vocals for t in job.tracks)


def run(force: bool = False) -> tuple[int, int, int]:
    """Reprocess done jobs. Returns (reprocessed, skipped, failed)."""
    rebuild(reset=True)   # index from disk first
    session = SessionLocal()
    try:
        candidates, skipped = [], 0
        for job in session.query(Job).filter(Job.status == JobStatus.done).all():
            if not force and _has_vocals(job):
                skipped += 1
            else:
                candidates.append(job.id)
    finally:
        session.close()

    done = failed = 0
    for jid in candidates:
        try:
            reprocess_job(jid)
            done += 1
        except Exception:  # noqa: BLE001
            failed += 1
            log.exception("Reprocess failed for %s", jid)
    log.info("Done — %d reprocessed, %d skipped, %d failed", done, skipped, failed)
    return done, skipped, failed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Reprocess the library to add a vocals stem.")
    parser.add_argument("--force", action="store_true",
                        help="reprocess even jobs that already have a vocals stem")
    args = parser.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_reprocess_api.py tests/test_reprocess_cli.py -v` → PASS.
Run: `.venv/bin/python -m pytest -q` → whole suite green.

- [ ] **Step 6: Commit**

```bash
git add songcoach/routes/api.py songcoach/reprocess.py tests/test_reprocess_api.py tests/test_reprocess_cli.py
git commit -m "feat(reprocess): POST /jobs/{id}/reprocess + python -m songcoach.reprocess CLI"
```

---

### Task 4: Player — vocals/backing rows + reprocess button

**Files:**
- Modify: `songcoach/static/js/player.js` (`KINDS` + reprocess wiring), `songcoach/templates/player.html` (reprocess icon + overlay)

**Interfaces:**
- Consumes: `POST /api/jobs/{id}/reprocess` (Task 3); existing `jobId`, `poll` patterns.

- [ ] **Step 1: Add VOCALS + BACKING to `KINDS`**

In `songcoach/static/js/player.js`, replace the `KINDS` block (and its stale
comment) with:

```javascript
// color = played (progress), dim = unplayed waveform, tuned for the light UI.
// NOTE: `original` == `drums` + `vocals` + `backing` (the full 4-stem mix); the
// stems are the real mixer, `original` is a mutually-exclusive REF full mix.
// `no_drums` is the LEGACY pre-reprocess stem (rendered only for un-reprocessed songs).
const KINDS = [
  { kind: "original",           name: "FULL SONG", sub: "reference mix",      color: "#6d45e6", dim: "#c9bcf5" },
  { kind: "drums",              name: "DRUMS",     sub: "the kit, solo",      color: "#e8760a", dim: "#f6cf9f" },
  { kind: "vocals",             name: "VOCALS",    sub: "the voice, solo",    color: "#d6336c", dim: "#f2b8cd" },
  { kind: "no_drums_no_vocals", name: "BACKING",   sub: "bass, keys & the rest", color: "#0e9e90", dim: "#a6ded7" },
  { kind: "no_drums",           name: "NO DRUMS",  sub: "play along",         color: "#0e9e90", dim: "#a6ded7" },
];
```

(The player already does `const track = byKind[meta.kind]; if (!track) return;`, so
a 4-stem job renders original/drums/vocals/backing and a legacy job renders
original/drums/no_drums.)

- [ ] **Step 2: Add the reprocess icon + overlay to `player.html`**

In `.console__controls`, after `#delete-open`, add:

```html
        <button id="reprocess-open" class="icon-btn" type="button" title="Re-separate (add vocals stem)" aria-label="Reprocess — add vocals stem"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg></button>
```

After the marker overlay (or the help overlay), add a reprocessing overlay:

```html
  <!-- REPROCESSING -->
  <div id="reprocess-overlay" class="edit-overlay" hidden>
    <div class="edit-card" role="dialog" aria-modal="true" aria-label="Reprocessing">
      <div class="rack-screws"><i></i><i></i><i></i><i></i></div>
      <span class="tape__label">Re-separating…</span>
      <p class="help-intro">Adding a vocals stem — this takes a minute. The player refreshes automatically when it's done.</p>
    </div>
  </div>
```

- [ ] **Step 3: Wire the reprocess flow in `player.js`**

Add near the edit/help/delete wiring at the bottom:

```javascript
document.getElementById("reprocess-open").addEventListener("click", async () => {
  if (!confirm("Re-separate this song to add a vocals stem? This takes a minute.")) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}/reprocess`, { method: "POST" });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      alert(d.detail || "Couldn't reprocess this recording.");
      return;
    }
    document.getElementById("reprocess-overlay").hidden = false;
    const timer = setInterval(async () => {
      try {
        const j = await (await fetch(`/api/jobs/${jobId}`)).json();
        if (j.status === "done") { clearInterval(timer); location.reload(); }
        else if (j.status === "failed") { clearInterval(timer); alert("Reprocess failed: " + (j.error || "")); location.reload(); }
      } catch {}
    }, 2000);
  } catch (err) {
    alert("Couldn't reprocess: " + err.message);
  }
});
```

- [ ] **Step 4: Verify (implementer: suite + render smoke-check; controller: Playwright)**

Do NOT run Playwright — the controller runs the browser acceptance. Implementer
verification:

1. `.venv/bin/python -m pytest -q` → whole suite green (frontend edit).
2. Render smoke-check — seed a 4-stem job with real short mp3s (use
   `vendor/ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 8 out.mp3` copied to
   `original.mp3`/`drums.mp3`/`vocals.mp3`/`no_drums_no_vocals.mp3`, and a meta.json
   listing all four tracks). Start a server, then:
```
curl -s http://127.0.0.1:8156/jobs/<id> | grep -o 'id="reprocess-\(open\|overlay\)"' | sort -u
curl -s http://127.0.0.1:8156/api/jobs/<id> | grep -o '"kind":"[a-z_]*"'
```
Expect `id="reprocess-open"`, `id="reprocess-overlay"`, and the four kinds
(`original`/`drums`/`vocals`/`no_drums_no_vocals`) in the job JSON. Kill + clean up.

**Controller Playwright acceptance** (isolated dir, real short-mp3 fixtures):
- A **4-stem** job renders four strips labelled FULL SONG / DRUMS / VOCALS /
  BACKING (deck must decode → wait for readiness).
- A **legacy 3-stem** job (original/drums/no_drums) renders three strips (FULL
  SONG / DRUMS / NO DRUMS).
- The `#reprocess-open` icon is present next to edit/delete; clicking it and
  accepting the confirm fires `POST …/reprocess` (200) and shows
  `#reprocess-overlay`, and the backend job status becomes `separating`. (Tear
  down before the real Demucs run finishes — the separation itself is covered by
  the backend tests.)

- [ ] **Step 5: Commit**

```bash
git add songcoach/static/js/player.js songcoach/templates/player.html
git commit -m "feat(player): VOCALS + BACKING rows and a reprocess button"
```

---

## Self-Review

**Spec coverage:**
- Separator emits drums/vocals/backing → Task 1. ✓
- TrackKind vocals + no_drums_no_vocals, legacy kept → Task 2 Step 1. ✓
- New-capture publish (4 stems, shared helper) → Task 2 Step 2. ✓
- `reprocess_job` (source original.mp3, drop legacy, preserve markers) → Task 2 Step 2 + test. ✓
- Serial queue reprocess dispatch → Task 2 Step 3 (+ existing-test signature fix). ✓
- `POST /reprocess` (404/409/200) → Task 3. ✓
- CLI (all done, skip-vocals unless --force) → Task 3. ✓
- Player VOCALS/BACKING rows + reprocess button/flow + legacy render → Task 4. ✓
- Demucs mocked in all tests → Tasks 1–3. ✓

**Placeholder scan:** No placeholders. (An earlier draft's illustrative "placeholder" line in Task 2 Step 2 was removed — the process_capture replacement is a single clean block.)

**Type/name consistency:** `SeparationResult{drums_path, vocals_path, backing_path}` used in separator + process + reprocess test. `_publish_stems(job, storage, deliverables)` consistent (no `session` arg — Task 2 Step 2's process_capture call passes `(job, storage, {...})`). `reprocess_job(job_id)`, `stem_queue.enqueue_reprocess`, `TrackKind.no_drums_no_vocals`, `cli.run(force)` consistent across tasks/tests. `#reprocess-open`/`#reprocess-overlay` match template↔JS.

**Note for implementers:**
- Task 2's `_publish_stems` takes `(job, storage, deliverables)` — NO `session` arg.
- Task 2 changes `_run_job`'s signature, so the two `tests/test_stem_queue.py` fakes MUST be updated to `def fake_run(job_id, task="process")` — else they break.
- Task 1's full-suite run may show a pre-existing process test failing if it referenced `sep.no_drums_path`; that's expected and resolved in Task 2 (which updates process_capture). If no such test exists, nothing to do.
