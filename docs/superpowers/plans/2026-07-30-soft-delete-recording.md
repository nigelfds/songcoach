# Soft-Delete a Library Recording — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user soft-delete a recording from its player view — mark the item's `meta.json` `"deleted": true`, refresh the DB so it leaves the library, and never touch the files on disk.

**Architecture:** A `deleted` flag in the sidecar (the source of truth). `rebuild()` skips flagged items; a `DELETE /api/jobs/{id}` endpoint marks the flag then `rebuild(reset=True)`; a 🗑 button on the player confirms and calls it.

**Tech Stack:** FastAPI, SQLAlchemy, pytest + `fastapi.testclient`, Jinja2, vanilla JS.

## Global Constraints

- **Run tests with** `.venv/bin/python -m pytest` (NOT `.venv/bin/pytest`).
- **Soft delete never touches files** — only `meta.json` gains `"deleted": true`; stems/thumbnail/capture are left as-is.
- **Read the data root dynamically** via the existing `metadata.job_dir`/`meta_path` (they read `settings.local_storage_dir`, so the `storage_dir` fixture applies).
- **Delete guard**: only `done`/`failed` (terminal) jobs are deletable; a non-terminal status → 409 (avoids racing `process_capture`'s final `write_meta`).
- **`JobStatus`** terminal values are `done`, `failed`; non-terminal are `recording`, `queued`, `separating`, `uploading`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Backend soft-delete (flag, rebuild skip, endpoint)

**Files:**
- Modify: `songcoach/metadata.py` (`mark_deleted`)
- Modify: `songcoach/rebuild.py` (skip deleted)
- Modify: `songcoach/routes/api.py` (`DELETE /api/jobs/{id}`)
- Test: `tests/test_delete_recording.py`

**Interfaces:**
- Consumes: `metadata.meta_path`, `metadata.read_meta`, `metadata.job_dir`, `metadata.write_meta`; `rebuild`; `SessionLocal`, `Job`, `JobStatus`.
- Produces:
  - `metadata.mark_deleted(job_id: str) -> bool` — sets `deleted=true` in the sidecar (atomic); `False` if no sidecar.
  - `rebuild()` no longer indexes a job whose sidecar has `deleted: true`.
  - `DELETE /api/jobs/{job_id}` → 204 (deleted), 404 (unknown / no sidecar), 409 (non-terminal).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_delete_recording.py
import json

import pytest
from fastapi.testclient import TestClient

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus
from songcoach.rebuild import rebuild


def _seed_job(jid="j1", status=JobStatus.done):
    """A job row + its sidecar + a stem file on disk (in the tmp storage_dir)."""
    s = SessionLocal()
    try:
        job = Job(id=jid, title="Song", artist="Artist", status=status)
        s.add(job)
        s.commit()
        d = metadata.job_dir(jid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "drums.mp3").write_bytes(b"audio")
        metadata.write_meta(job)
    finally:
        s.close()
    return jid


def _sidecar(jid):
    return json.loads(metadata.meta_path(jid).read_text(encoding="utf-8"))


# --- metadata.mark_deleted -------------------------------------------------

def test_mark_deleted_sets_flag_keeps_other_keys(storage_dir):
    jid = _seed_job()
    assert metadata.mark_deleted(jid) is True
    data = _sidecar(jid)
    assert data["deleted"] is True
    assert data["title"] == "Song"      # other keys intact


def test_mark_deleted_missing_sidecar(storage_dir):
    assert metadata.mark_deleted("ghost") is False


# --- rebuild skips deleted -------------------------------------------------

def test_rebuild_skips_deleted(storage_dir):
    _seed_job("keep")
    _seed_job("gone")
    metadata.mark_deleted("gone")
    rebuild(reset=True)
    s = SessionLocal()
    try:
        assert s.get(Job, "keep") is not None
        assert s.get(Job, "gone") is None
    finally:
        s.close()


# --- DELETE endpoint -------------------------------------------------------

@pytest.fixture
def client(storage_dir):
    from songcoach.main import app
    return TestClient(app)


def test_delete_done_job_soft(client, storage_dir):
    jid = _seed_job("d1")
    r = client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 204
    s = SessionLocal()
    try:
        assert s.get(Job, jid) is None          # gone from the index
    finally:
        s.close()
    assert _sidecar(jid)["deleted"] is True      # sidecar flagged
    assert (metadata.job_dir(jid) / "drums.mp3").exists()   # files untouched


def test_delete_unknown_404(client, storage_dir):
    assert client.delete("/api/jobs/nope").status_code == 404


def test_delete_while_processing_409(client, storage_dir):
    jid = _seed_job("p1", status=JobStatus.separating)
    r = client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 409
    assert "deleted" not in _sidecar(jid)        # sidecar NOT modified


def test_deleted_job_player_page_404(client, storage_dir):
    jid = _seed_job("pl1")
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert client.get(f"/jobs/{jid}").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_delete_recording.py -v`
Expected: FAIL — `AttributeError: module 'songcoach.metadata' has no attribute 'mark_deleted'` / 405 on DELETE (route absent).

- [ ] **Step 3: Implement `metadata.mark_deleted`**

Add to `songcoach/metadata.py` (after `write_meta`; `json` and `Path` are already imported):

```python
def mark_deleted(job_id: str) -> bool:
    """Soft-delete: set ``deleted: true`` in the job's sidecar, atomically.

    The stem files / thumbnail / capture on disk are left untouched. Returns
    ``False`` if there is no sidecar to mark.
    """
    path = meta_path(job_id)
    if not path.exists():
        return False
    data = read_meta(path)
    data["deleted"] = True
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic swap on POSIX
    return True
```

- [ ] **Step 4: Make `rebuild()` skip deleted items**

In `songcoach/rebuild.py`, inside the scan loop, add the skip right before the
`_job_from_meta` call. Change:

```python
            job = _job_from_meta(data, meta_file.parent)
            if job is None:
                continue
```

to:

```python
            if data.get("deleted"):
                continue   # soft-deleted → not indexed, absent from the library
            job = _job_from_meta(data, meta_file.parent)
            if job is None:
                continue
```

- [ ] **Step 5: Add the `DELETE` endpoint**

In `songcoach/routes/api.py`, extend the imports:
- change `from fastapi import APIRouter, Depends, HTTPException, UploadFile` to also import `Response`:
  `from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile`
- change `from ..db import get_session` to `from ..db import get_session, SessionLocal`
- add `from ..rebuild import rebuild`

Add the endpoint (near the other `/jobs/{job_id}` routes):

```python
@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    """Soft-delete a recording: flag its sidecar and drop it from the index.

    The files on disk are left untouched. Its own session is closed before the
    rebuild so the drop-and-recreate doesn't race an open transaction.
    """
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        status = job.status if job is not None else None
    finally:
        session.close()

    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if status not in (JobStatus.done, JobStatus.failed):
        raise HTTPException(status_code=409, detail="Can't delete while it's still processing.")
    if not metadata.mark_deleted(job_id):
        raise HTTPException(status_code=404, detail="Recording not found on disk")

    rebuild(reset=True)   # refresh the index — the deleted item drops out
    return Response(status_code=204)
```

- [ ] **Step 6: Run the tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_delete_recording.py -v`
Expected: PASS (all 7).
Run: `.venv/bin/python -m pytest -q`
Expected: whole suite green (73 prior + 7 new = 80).

- [ ] **Step 7: Commit**

```bash
git add songcoach/metadata.py songcoach/rebuild.py songcoach/routes/api.py tests/test_delete_recording.py
git commit -m "feat(library): soft-delete recordings (meta flag + rebuild skip + DELETE endpoint)"
```

---

### Task 2: Player delete button

**Files:**
- Modify: `songcoach/templates/player.html` (delete `.icon-btn`)
- Modify: `songcoach/static/js/player.js` (wiring)

**Interfaces:**
- Consumes: `DELETE /api/jobs/{job_id}` (Task 1); `jobId` (already `app.dataset.jobId`).

- [ ] **Step 1: Add the delete button**

In `songcoach/templates/player.html`, the controls are:

```html
      <div class="console__controls">
        <button id="help-open" class="icon-btn" type="button" title="How this works" aria-label="Help — how this works">?</button>
        <button id="edit-open" class="icon-btn" type="button" title="Edit details" aria-label="Edit details">✎</button>
      </div>
```

Add a delete button after the edit button (still inside `.console__controls`):

```html
        <button id="delete-open" class="icon-btn" type="button" title="Delete recording" aria-label="Delete recording">🗑</button>
```

- [ ] **Step 2: Wire it in `player.js`**

In `songcoach/static/js/player.js`, near the edit/help wiring at the bottom
(after the `help-close` / `helpOverlay` listeners around line 650-652), add:

```javascript
document.getElementById("delete-open").addEventListener("click", async () => {
  if (!confirm("Delete this recording? It's removed from your library. " +
               "The audio files stay on disk.")) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (res.status === 204) { location.href = "/"; return; }
    const d = await res.json().catch(() => ({}));
    alert(d.detail || "Could not delete this recording.");
  } catch (err) {
    alert("Could not delete this recording: " + err.message);
  }
});
```

- [ ] **Step 3: Verify (implementer: suite + render smoke-check; controller: Playwright)**

Do NOT run Playwright — the controller runs the browser acceptance. Implementer
verification:

1. `.venv/bin/python -m pytest -q` → 80 passed (unchanged by a template/JS edit).
2. Render smoke-check (needs a `done` job to view a player; the real `./data`
   has done recordings):
```
.venv/bin/python -m uvicorn songcoach.main:app --port 8148 >/tmp/del.log 2>&1 &
SRV=$!; sleep 4
JOB=$(curl -s http://127.0.0.1:8148/api/jobs | .venv/bin/python -c "import sys,json; j=[x for x in json.load(sys.stdin) if x['status']=='done']; print(j[0]['id'] if j else '')")
curl -s "http://127.0.0.1:8148/jobs/$JOB" | grep -o 'id="delete-open"'
kill $SRV
```
Expect: `id="delete-open"` present on the player page.

**Controller Playwright acceptance** (isolated data dir with a seeded `done` job,
so the delete is against throwaway data):
- Open the player for a done job → assert the 🗑 button is present next to `?`/`✎`.
- Click it; the `confirm()` dialog appears with the warning text → accept it →
  the DELETE fires (204) and the page navigates to `/`.
- The library no longer lists that recording; its `meta.json` on disk has
  `deleted: true` and the stem files still exist.

- [ ] **Step 4: Commit**

```bash
git add songcoach/templates/player.html songcoach/static/js/player.js
git commit -m "feat(player): delete-recording button with confirm"
```

---

## Self-Review

**Spec coverage:**
- `deleted` flag in `meta.json`, files untouched → Task 1 (`mark_deleted`). ✓
- `rebuild()` skips deleted → Task 1 Step 4. ✓
- `DELETE /api/jobs/{id}` (404/409/204) + refresh DB via `rebuild(reset=True)` → Task 1 Step 5. ✓
- Delete guard while processing → Task 1 Step 5 (`status not in (done, failed)`). ✓
- 🗑 button next to edit/help + confirm + back to library → Task 2. ✓
- Tests (mark_deleted, rebuild-skip, endpoint 204/404/409, files-on-disk, player 404) → Task 1 Step 1. ✓

**Placeholder scan:** No TBD/TODO; concrete code in every step.

**Type/name consistency:** `metadata.mark_deleted(job_id) -> bool`, `data.get("deleted")`, `DELETE /api/jobs/{job_id}` → 204, `#delete-open`, `jobId` used identically across tasks. `Response`, `SessionLocal`, `rebuild` imports named in Task 1 Step 5.

**Notes for the implementer:**
- The endpoint deliberately does NOT use `Depends(get_session)`; it opens/closes its own `SessionLocal` before `rebuild(reset=True)` (which drops+recreates all tables) so the drop doesn't race an open request-scoped session on SQLite.
- `Response(status_code=204)` returns an empty body (a 204 must have no body).
- The `client` fixture (`from songcoach.main import app`) reuses the app singleton; `rebuild` reads `settings.local_storage_dir` dynamically, so each test's DELETE rebuilds against that test's tmp `storage_dir`.
