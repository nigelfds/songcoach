# Resume Stemming from Orphaned Recordings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user re-stem an existing capture (instead of re-recording) via a manual Retry action, with failed recordings staying discoverable and correctly labeled across app restarts.

**Architecture:** Reuse the existing `jobs/{id}/meta.json` sidecar as the durable metadata store. Persist failures to it (currently only written on success), add the missing `error` field, and let the existing startup `rebuild()` surface failed jobs. A retry endpoint re-runs `process_capture()` against the surviving `recordings/{id}/capture.m4a`; a button on the player's failure screen triggers it.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), Jinja2 + vanilla JS front end, pytest + httpx for tests.

## Global Constraints

- Python 3.11; venv at `.venv` — run tests with `.venv/bin/pytest`.
- Reuse existing patterns: sidecar I/O lives in `songcoach/metadata.py`; disk paths come from `settings.local_storage_dir` (read dynamically, so tests monkeypatch it).
- Tests MUST NOT run real Demucs — monkeypatch `songcoach.jobs.enqueue_processing`.
- Only `failed` jobs are resumable. Orphan detection trigger = `recordings/{id}/capture.m4a` present with no published stems.
- Commit after each task. End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Do NOT modify `SongCoach.spec` here (the numpy fix already living there is separate, uncommitted work).

---

### Task 1: Persist `error` in the sidecar (+ test scaffolding)

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_metadata.py`
- Modify: `songcoach/metadata.py` (in `to_dict`, ~line 53-72)

**Interfaces:**
- Produces: `metadata.to_dict(job)` now includes key `"error"` (str | None). `tests/conftest.py` exposes fixtures `storage_dir` (a monkeypatched `settings.local_storage_dir` tmp dir) and `db` (a fresh-tables `Session`).

- [ ] **Step 1: Add dev dependencies**

Create `requirements-dev.txt`:

```
pytest>=8
httpx>=0.27
```

Install: `.venv/bin/pip install -r requirements-dev.txt`

- [ ] **Step 2: Write the test harness**

Create `tests/conftest.py`. The env vars MUST be set before importing songcoach, so the DB engine and settings resolve to a throwaway location instead of the repo `./data` / `.env` values:

```python
"""Test harness: redirect DB + storage to a throwaway location, before songcoach imports read them."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="songcoach-tests-")
# os.environ wins over the repo .env in pydantic-settings' precedence order.
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["LOCAL_STORAGE_DIR"] = f"{_TMP}/data"

import pytest

from songcoach.config import settings
from songcoach.db import Base, SessionLocal, engine, init_db


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    """A per-test data dir; functions read settings.local_storage_dir dynamically."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(settings, "local_storage_dir", d)
    return d


@pytest.fixture
def db():
    """A session over freshly recreated tables."""
    Base.metadata.drop_all(bind=engine)
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_metadata.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_metadata.py -v`
Expected: FAIL — `KeyError: 'error'` (the key isn't emitted yet).

- [ ] **Step 5: Add the `error` field**

In `songcoach/metadata.py`, inside `to_dict`, add the `error` line right after `status`:

```python
        "status": job.status.value,
        "error": job.error,
        "created_at": _iso(job.created_at),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_metadata.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt tests/conftest.py tests/test_metadata.py songcoach/metadata.py
git commit -m "feat: persist error in job metadata sidecar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Write the sidecar on failure

**Files:**
- Modify: `songcoach/pipeline/process.py` (`_fail`, ~line 112-118)
- Create: `tests/test_process_fail.py`

**Interfaces:**
- Consumes: `metadata.write_meta`, `metadata.meta_path`, `metadata.read_meta` (Task 1).
- Produces: after `_fail(session, job_id, message)`, `jobs/{job_id}/meta.json` exists with `status="failed"` and the error.

- [ ] **Step 1: Write the failing test**

Create `tests/test_process_fail.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_process_fail.py -v`
Expected: FAIL — `FileNotFoundError` (no sidecar written on failure yet).

- [ ] **Step 3: Write the sidecar in `_fail`**

Replace `_fail` in `songcoach/pipeline/process.py` with:

```python
def _fail(session, job_id: str, message: str) -> None:
    job = session.get(Job, job_id)
    if job is not None:
        job.status = JobStatus.failed
        job.error = message
        session.commit()
        # Persist the failure to the durable sidecar so it survives the startup
        # rebuild (which re-indexes from jobs/*/meta.json) as a failed, retryable job.
        try:
            metadata.write_meta(job)
        except OSError:
            log.warning("Could not write failure sidecar for %s", job_id, exc_info=True)
```

(`metadata` and `log` are already imported at the top of `process.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_process_fail.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add songcoach/pipeline/process.py tests/test_process_fail.py
git commit -m "feat: write failed-status sidecar so failures survive restart

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Rebuild fallback scan for sidecar-less captures

**Files:**
- Modify: `songcoach/rebuild.py` (`rebuild`, ~line 74-106; add helper)
- Create: `tests/test_rebuild_orphans.py`

**Interfaces:**
- Consumes: `settings.local_storage_dir`, `metadata.write_meta` (Task 1), `Job`, `JobStatus`.
- Produces: `rebuild(reset=True)` indexes `recordings/*/capture.m4a` whose id has no `jobs/` entry as a `failed` Job with a fallback title; ids already indexed from `jobs/` are not duplicated.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rebuild_orphans.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rebuild_orphans.py -v`
Expected: FAIL — `test_rebuild_indexes_sidecarless_orphan` fails (job is None; orphans not scanned).

- [ ] **Step 3: Add the orphan scan**

In `songcoach/rebuild.py`, add this import near the top (alongside the existing `from datetime import datetime`):

```python
from .config import settings
```

(`settings` may already be imported — if so, skip. `datetime` is already imported.)

Add this helper above `rebuild`:

```python
def _index_orphan_captures(session, indexed_ids: set[str]) -> int:
    """Index captures in recordings/ that have no jobs/ entry as resumable failed jobs."""
    rec_root = Path(settings.local_storage_dir) / "recordings"
    if not rec_root.is_dir():
        return 0
    count = 0
    for capture in sorted(rec_root.glob("*/capture.m4a")):
        job_id = capture.parent.name
        if job_id in indexed_ids:
            continue
        mtime = datetime.fromtimestamp(capture.stat().st_mtime)
        session.merge(Job(
            id=job_id,
            title=f"Untitled recording {mtime:%b %-d, %-I:%M %p}",
            status=JobStatus.failed,
            progress=0,
            error="Stemming didn't finish — retry to resume.",
            created_at=mtime,
        ))
        count += 1
    return count
```

In `rebuild`, track indexed ids and call the helper before the commit. Replace the loop body + commit region:

```python
    session = SessionLocal()
    count = 0
    try:
        indexed_ids: set[str] = set()
        for meta_file in sorted(jobs_root.glob(f"*/{META_FILENAME}")):
            try:
                data = read_meta(meta_file)
            except (ValueError, OSError) as exc:
                log.warning("Skipping unreadable %s: %s", meta_file, exc)
                continue
            job = _job_from_meta(data, meta_file.parent)
            if job is None:
                continue
            session.merge(job)
            indexed_ids.add(job.id)
            count += 1
        count += _index_orphan_captures(session, indexed_ids)
        session.commit()
    finally:
        session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rebuild_orphans.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `.venv/bin/pytest -v`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add songcoach/rebuild.py tests/test_rebuild_orphans.py
git commit -m "feat: rebuild indexes orphaned captures as resumable failed jobs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Retry endpoint + `resumable` flag

**Files:**
- Modify: `songcoach/routes/api.py` (imports; `JobOut` ~line 45-55; `_serialize` ~line 58-74; new endpoint)
- Create: `tests/test_retry_api.py`

**Interfaces:**
- Consumes: `capture_dir` from `..pipeline.recorder`; `jobs.enqueue_processing`; `recording.is_recording`; `JobStatus`.
- Produces: `POST /api/jobs/{id}/retry` → 200 with reset job (status `queued`) + enqueues processing; 404 unknown; 409 not-failed / capture-missing / recording-in-progress. `JobOut.resumable: bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retry_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


@pytest.fixture
def client(storage_dir, monkeypatch):
    calls = []
    from songcoach import jobs
    monkeypatch.setattr(jobs, "enqueue_processing", lambda jid: calls.append(jid))
    from songcoach.main import app
    c = TestClient(app)
    c.enqueue_calls = calls
    return c


def _failed_job(with_capture, storage_dir):
    s = SessionLocal()
    job = Job(title="T", status=JobStatus.failed, error="boom")
    s.add(job)
    s.commit()
    jid = job.id
    s.close()
    if with_capture:
        rec = storage_dir / "recordings" / jid
        rec.mkdir(parents=True)
        (rec / "capture.m4a").write_bytes(b"x")
    return jid


def test_retry_resets_and_enqueues(client, storage_dir):
    jid = _failed_job(True, storage_dir)
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    assert client.enqueue_calls == [jid]


def test_retry_409_when_capture_missing(client, storage_dir):
    jid = _failed_job(False, storage_dir)
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 409
    assert client.enqueue_calls == []


def test_serialize_exposes_resumable(client, storage_dir):
    jid = _failed_job(True, storage_dir)
    body = client.get(f"/api/jobs/{jid}").json()
    assert body["resumable"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_retry_api.py -v`
Expected: FAIL — 404/`resumable` KeyError (endpoint + field don't exist).

- [ ] **Step 3: Update imports in `routes/api.py`**

Change the recorder import and the top-level package import:

```python
from .. import fetch_thumbnails, jobs, metadata, recording, youtube
```

```python
from ..pipeline.recorder import RecorderError, capture_dir
```

- [ ] **Step 4: Add `resumable` to `JobOut` and `_serialize`**

Add the field to `JobOut` (after `error`):

```python
    error: str | None
    resumable: bool
    tracks: list[TrackOut]
```

In `_serialize`, compute it and pass it (before the `return`):

```python
    resumable = (
        job.status == JobStatus.failed
        and (capture_dir(job.id) / "capture.m4a").exists()
    )
    return JobOut(
        id=job.id, status=job.status.value, progress=job.progress,
        title=job.title, artist=job.artist, youtube_url=job.youtube_url,
        thumbnail_url=thumbnail_url,
        duration_seconds=job.duration_seconds, error=job.error,
        resumable=resumable, tracks=tracks,
    )
```

- [ ] **Step 5: Add the retry endpoint**

Add after `get_job` / `update_job` in `routes/api.py`:

```python
@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, session: Session = Depends(get_session)):
    """Re-run separation for a failed job whose capture is still on disk."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    if job.status != JobStatus.failed:
        raise HTTPException(status_code=409, detail="Only failed recordings can be retried.")
    if not (capture_dir(job_id) / "capture.m4a").exists():
        raise HTTPException(status_code=409, detail="This recording is no longer available.")
    job.status = JobStatus.queued
    job.progress = 10
    job.error = None
    session.commit()
    jobs.enqueue_processing(job_id)
    return _serialize(job)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_retry_api.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: all passing.

- [ ] **Step 8: Commit**

```bash
git add songcoach/routes/api.py tests/test_retry_api.py
git commit -m "feat: add retry endpoint and resumable flag

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Retry button on the failure screen

**Files:**
- Modify: `songcoach/templates/player.html` (~line 15-16)
- Modify: `songcoach/static/js/player.js` (`poll` ~line 54-56; `showError` ~line 72-79; new handler)

**Interfaces:**
- Consumes: `POST /api/jobs/{id}/retry`; `JobOut.resumable` (Task 4). `jobId` is already defined at the top of `player.js` (from `#app[data-job-id]`).
- Produces: a "Retry stemming" button shown on `resumable` failed jobs that re-triggers stemming and resumes polling.

This task's verification is manual (browser JS + the real fixture); it doubles as the end-to-end check of the numpy fix.

- [ ] **Step 1: Add the button to the processing panel**

In `songcoach/templates/player.html`, replace the lone start-over link (line ~16):

```html
      <p class="processing__error" id="processing-error" hidden></p>
      <a class="link-back" href="/">‹ start over</a>
```

with:

```html
      <p class="processing__error" id="processing-error" hidden></p>
      <div class="processing__actions">
        <button id="retry-btn" class="btn-primary" type="button" hidden>Retry stemming</button>
        <a class="link-back" href="/">‹ start over</a>
      </div>
```

- [ ] **Step 2: Pass the job to `showError` and reveal the button**

In `songcoach/static/js/player.js`, in `poll`, change the failed branch:

```javascript
    if (job.status === "failed") {
      showError(job);
      return;
    }
```

Replace `showError` with:

```javascript
function showError(job) {
  const el = document.getElementById("processing-error");
  el.textContent = job.error || "Processing failed.";
  el.hidden = false;
  document.getElementById("stage-tag").textContent = "FAILED";
  document.getElementById("processing-hint").hidden = true;
  document.getElementById("meter-fill").style.background = "var(--red)";
  document.getElementById("retry-btn").hidden = !job.resumable;
}
```

- [ ] **Step 3: Wire the retry handler**

Add below `showError` in `player.js`:

```javascript
document.getElementById("retry-btn").addEventListener("click", async () => {
  const retry = document.getElementById("retry-btn");
  retry.disabled = true;
  try {
    const res = await fetch(`/api/jobs/${jobId}/retry`, { method: "POST" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Couldn't retry.");
    }
    // Reset the processing UI and resume polling from queued.
    document.getElementById("processing-error").hidden = true;
    document.getElementById("processing-hint").hidden = false;
    document.getElementById("meter-fill").style.background = "";
    retry.hidden = true;
    retry.disabled = false;
    poll();
  } catch (err) {
    document.getElementById("processing-error").textContent = err.message;
    retry.disabled = false;
  }
});
```

- [ ] **Step 4: Manual verification (dev server first — fast loop)**

The repo `.env` points storage at `./data`, where the fixture lives, so the dev server sees it directly.

```bash
.venv/bin/python -m uvicorn songcoach.main:app --reload
```

Open `http://localhost:8000/jobs/bddaf9401b7e41e5841165a4c9c3e6ee`. Expect: FAILED screen showing the numpy error, with a **Retry stemming** button. (Dev separation will *succeed* here because `numpy.core.multiarray` imports fine outside the bundle — this verifies the UI + endpoint wiring and that a retry runs to a playable result.)

- [ ] **Step 5: Manual verification (built app — the numpy-fix check)**

Rebuild with the numpy-fixed spec and confirm the collected submodule landed:

```bash
scripts/build_macapp.sh
find dist/SongCoach.app -path '*numpy/core/multiarray.py'   # must print a path
```

Launch the built app **from the repo directory** (so it reads `.env` → `./data` and sees the fixture):

```bash
./dist/SongCoach.app/Contents/MacOS/SongCoach
```

Open the same job, click **Retry stemming**. Expect: stemming proceeds past `get_model()` (no `numpy.core.multiarray` error), completes, and the player loads the stems. This confirms the numpy fix and the resume feature together.

- [ ] **Step 6: Commit**

```bash
git add songcoach/templates/player.html songcoach/static/js/player.js
git commit -m "feat: retry button on the failure screen

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** error-in-sidecar (T1), write-on-failure + stuck-`queued` fix (T2), rebuild surfacing incl. fallback scan (T3), retry endpoint + `resumable` (T4), UI button (T5), fixture end-to-end + numpy check (T5). All spec sections covered.
- **Scope deviation from spec:** the Retry button lives only on the player failure screen, not also as a separate library-list control — the library row is already a full-row link to that screen, so a second control would be redundant (YAGNI). Behavior the user approved (retry a failed recording) is unchanged.
- **Type consistency:** `resumable: bool` defined in `JobOut` (T4) and consumed in `player.js` as `job.resumable` (T5); `capture_dir` imported once (T4) and reused by endpoint + `_serialize`.
