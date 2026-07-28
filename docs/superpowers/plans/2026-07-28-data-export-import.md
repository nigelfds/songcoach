# Data Export / Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user export their whole `data/` library to a `.zip` and import one back with `cp -rf` merge semantics, all from the web UI.

**Architecture:** A new `songcoach/archive.py` owns all zip/unzip + safety logic (independent of FastAPI, unit-tested against a tmp `data/`). Two thin API endpoints in `routes/api.py` wrap it. A small `static/js/library.js` + two buttons in the landing header drive it. Import lays files down over `data/` then calls the existing `rebuild()`; the DB is never in the archive.

**Tech Stack:** Python 3.11, FastAPI, `zipfile` (stdlib), pytest + `fastapi.testclient`, vanilla JS.

## Global Constraints

- **Run tests with** `.venv/bin/python -m pytest` (NOT `.venv/bin/pytest` — repo root isn't on `sys.path`).
- **Data root is dynamic:** always read `Path(settings.local_storage_dir)` at call time (never cache at import) so the `storage_dir` test fixture's monkeypatch takes effect.
- **Archive layout mirrors `data/`:** members are `jobs/<id>/...`, `recordings/<id>/...`, plus a root `songcoach-export.json` manifest.
- **`cp -rf` semantics:** overwrite files on conflict, never delete anything already on disk.
- **`ZIP_STORED`** (no compression) for export — mp3s are already compressed.
- **Safety:** only extract members under `jobs/` and `recordings/`; reject any member whose resolved path escapes the data root (zip-slip).
- **Recording gate:** both endpoints return 409 when `recording.is_recording()`.

---

### Task 1: `archive.py` — `build_export`

**Files:**
- Create: `songcoach/archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: `songcoach.config.settings.local_storage_dir`.
- Produces:
  - `MANIFEST_NAME = "songcoach-export.json"` (module constant)
  - `class ArchiveError(Exception)`
  - `@dataclass class ImportResult: added: int; updated: int`
  - `build_export(dest_zip: Path) -> int` — writes a `ZIP_STORED` zip of `data/jobs` + `data/recordings` + manifest to `dest_zip`; returns the count of `jobs/<id>/` recordings included.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_archive.py
import io
import json
import zipfile
from pathlib import Path

from songcoach import archive


def _job(storage_dir: Path, jid: str):
    d = storage_dir / "jobs" / jid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": jid, "title": "T", "status": "done"}))
    (d / "original.mp3").write_bytes(b"audio")


def test_build_export_zips_jobs_and_manifest(storage_dir, tmp_path):
    _job(storage_dir, "job1")
    (storage_dir / "recordings" / "rec1").mkdir(parents=True)
    (storage_dir / "recordings" / "rec1" / "capture.m4a").write_bytes(b"cap")

    dest = tmp_path / "out.zip"
    n = archive.build_export(dest)

    assert n == 1
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "jobs/job1/meta.json" in names
        assert "jobs/job1/original.mp3" in names
        assert "recordings/rec1/capture.m4a" in names
        assert archive.MANIFEST_NAME in names
        manifest = json.loads(zf.read(archive.MANIFEST_NAME))
        assert manifest["app"] == "SongCoach"
        assert manifest["jobs"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_archive.py::test_build_export_zips_jobs_and_manifest -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'songcoach.archive'`.

- [ ] **Step 3: Write minimal implementation**

```python
# songcoach/archive.py
"""Export/import the data/ library as a .zip.

data/jobs/<id>/ (stems + meta.json + thumbnail) and data/recordings/<id>/ are the
source of truth; songcoach.db is a disposable index. So an export is just a zip of
data/, and an import lays files back down and rebuilds the cache.
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .config import settings

log = logging.getLogger("songcoach.archive")

MANIFEST_NAME = "songcoach-export.json"
_TOP_DIRS = ("jobs", "recordings")


class ArchiveError(Exception):
    """The upload isn't a usable SongCoach archive."""


@dataclass
class ImportResult:
    added: int
    updated: int


def _data_root() -> Path:
    return Path(settings.local_storage_dir)


def build_export(dest_zip: Path) -> int:
    """Zip data/jobs + data/recordings + a manifest into dest_zip. Return job count."""
    root = _data_root()
    job_count = sum(1 for p in (root / "jobs").glob("*") if p.is_dir())
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_STORED) as zf:
        for top in _TOP_DIRS:
            base = root / top
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.name != ".DS_Store":
                    zf.write(path, arcname=str(path.relative_to(root)))
        manifest = {
            "app": "SongCoach",
            "schema": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "jobs": job_count,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    return job_count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_archive.py::test_build_export_zips_jobs_and_manifest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add songcoach/archive.py tests/test_archive.py
git commit -m "feat(archive): export data/ library to a zip"
```

---

### Task 2: `archive.py` — `import_archive`

**Files:**
- Modify: `songcoach/archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: `build_export`, `ImportResult`, `ArchiveError`, `MANIFEST_NAME` from Task 1; `songcoach.rebuild.rebuild`.
- Produces: `import_archive(zip_path: Path) -> ImportResult` — safe-extracts `jobs/`+`recordings/` members over `data/` (`cp -rf`), runs `rebuild(reset=True)`, returns counts (`added` = job ids not previously on disk, `updated` = job ids that were).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_archive.py
import pytest


def test_import_round_trip_into_empty_dir(storage_dir, tmp_path, monkeypatch):
    # Export from a populated dir...
    _job(storage_dir, "job1")
    dest = tmp_path / "out.zip"
    archive.build_export(dest)

    # ...then import into a fresh empty data dir.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(archive.settings, "local_storage_dir", empty)

    result = archive.import_archive(dest)

    assert result.added == 1
    assert result.updated == 0
    assert (empty / "jobs" / "job1" / "meta.json").exists()
    assert (empty / "jobs" / "job1" / "original.mp3").read_bytes() == b"audio"


def test_import_cp_rf_overwrites_conflict_keeps_others(storage_dir, tmp_path):
    # Local library has job1 (with an extra stem) and an unrelated job2.
    _job(storage_dir, "job1")
    (storage_dir / "jobs" / "job1" / "drums.mp3").write_bytes(b"local-drums")
    _job(storage_dir, "job2")

    # Archive re-exports job1 only, with a different original.mp3.
    src = tmp_path / "src"
    (src / "jobs" / "job1").mkdir(parents=True)
    (src / "jobs" / "job1" / "meta.json").write_text('{"id":"job1","status":"done"}')
    (src / "jobs" / "job1" / "original.mp3").write_bytes(b"archive-audio")
    zpath = tmp_path / "in.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src / "jobs" / "job1" / "meta.json", "jobs/job1/meta.json")
        zf.write(src / "jobs" / "job1" / "original.mp3", "jobs/job1/original.mp3")

    result = archive.import_archive(zpath)

    assert result.added == 0
    assert result.updated == 1
    # Conflict file overwritten...
    assert (storage_dir / "jobs" / "job1" / "original.mp3").read_bytes() == b"archive-audio"
    # ...local-only extra stem kept (cp -rf never deletes)...
    assert (storage_dir / "jobs" / "job1" / "drums.mp3").read_bytes() == b"local-drums"
    # ...unrelated job untouched.
    assert (storage_dir / "jobs" / "job2" / "meta.json").exists()


def test_import_rejects_zip_slip(storage_dir, tmp_path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("jobs/../../evil.txt", "pwned")
        zf.writestr("/etc/passwd-ish", "pwned")
    archive.import_archive(zpath)
    assert not (tmp_path / "evil.txt").exists()
    assert not (storage_dir.parent / "evil.txt").exists()


def test_import_ignores_non_whitelisted_members(storage_dir, tmp_path):
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("secrets/creds.txt", "nope")
        zf.writestr("jobs/j/meta.json", '{"id":"j","status":"done"}')
    archive.import_archive(zpath)
    assert not (storage_dir / "secrets").exists()
    assert (storage_dir / "jobs" / "j" / "meta.json").exists()


def test_import_manifestless_zip(storage_dir, tmp_path):
    zpath = tmp_path / "plain.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("jobs/j/meta.json", '{"id":"j","status":"done"}')
    result = archive.import_archive(zpath)
    assert result.added == 1


def test_import_non_zip_raises(storage_dir, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(archive.ArchiveError):
        archive.import_archive(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_archive.py -k import -v`
Expected: FAIL with `AttributeError: module 'songcoach.archive' has no attribute 'import_archive'`.

- [ ] **Step 3: Write minimal implementation**

Append to `songcoach/archive.py`:

```python
def _within(target: Path, root: Path) -> bool:
    try:
        return target.resolve().is_relative_to(root.resolve())
    except (ValueError, OSError):
        return False


def import_archive(zip_path: Path) -> ImportResult:
    """Extract jobs/+recordings/ members over data/ (cp -rf), rebuild the cache."""
    from .rebuild import rebuild  # local import avoids a cycle at module load

    root = _data_root()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That doesn't look like a SongCoach export.") from exc

    archive_job_ids: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, Path]] = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if not parts or parts[0] not in _TOP_DIRS:
                continue  # whitelist: ignore manifest + anything else
            target = root / info.filename
            if not _within(target, root):
                log.warning("Skipping unsafe archive member: %s", info.filename)
                continue
            if parts[0] == "jobs" and len(parts) >= 2:
                archive_job_ids.add(parts[1])
            members.append((info, target))

        pre_existing = {j for j in archive_job_ids if (root / "jobs" / j).is_dir()}

        for info, target in members:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    rebuild(reset=True)
    return ImportResult(added=len(archive_job_ids - pre_existing), updated=len(pre_existing))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_archive.py -v`
Expected: PASS (all archive tests, export + import).

- [ ] **Step 5: Commit**

```bash
git add songcoach/archive.py tests/test_archive.py
git commit -m "feat(archive): import a zip over data/ with cp -rf merge + rebuild"
```

---

### Task 3: API endpoints

**Files:**
- Modify: `songcoach/routes/api.py`
- Test: `tests/test_archive_api.py`

**Interfaces:**
- Consumes: `archive.build_export`, `archive.import_archive`, `archive.ArchiveError` (Tasks 1–2); `recording.is_recording`.
- Produces:
  - `GET /api/export` → `FileResponse` (a `.zip`), or 409 while recording.
  - `POST /api/import` (multipart field `file`) → `{"added": int, "updated": int}`, or 409 while recording, or 422 on a bad archive.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_archive_api.py
import io
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(storage_dir, monkeypatch):
    from songcoach.main import app
    return TestClient(app)


def _job(storage_dir, jid):
    d = storage_dir / "jobs" / jid
    d.mkdir(parents=True)
    (d / "meta.json").write_text('{"id":"%s","status":"done"}' % jid)
    (d / "original.mp3").write_bytes(b"a")


def test_export_returns_zip(client, storage_dir):
    _job(storage_dir, "j1")
    r = client.get("/api/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "jobs/j1/meta.json" in zf.namelist()


def test_export_409_while_recording(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    assert client.get("/api/export").status_code == 409


def test_import_merges_and_returns_counts(client, storage_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("jobs/j9/meta.json", '{"id":"j9","status":"done"}')
    buf.seek(0)
    r = client.post("/api/import", files={"file": ("x.zip", buf, "application/zip")})
    assert r.status_code == 200
    assert r.json() == {"added": 1, "updated": 0}
    assert (storage_dir / "jobs" / "j9" / "meta.json").exists()


def test_import_409_while_recording(client, storage_dir, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("jobs/j/meta.json", "{}")
    buf.seek(0)
    r = client.post("/api/import", files={"file": ("x.zip", buf, "application/zip")})
    assert r.status_code == 409


def test_import_422_on_bad_archive(client, storage_dir):
    r = client.post("/api/import", files={"file": ("x.zip", io.BytesIO(b"nope"), "application/zip")})
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_archive_api.py -v`
Expected: FAIL — routes return 404/405 (endpoints don't exist yet).

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `songcoach/routes/api.py` (alongside the existing ones):

```python
import os
import tempfile
from datetime import date

from fastapi import UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import archive
```

Add these endpoints at the end of `songcoach/routes/api.py`:

```python
@router.get("/export")
def export_data():
    """Download the whole data/ library as a .zip."""
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    archive.build_export(Path(tmp))
    filename = f"SongCoach-export-{date.today():%Y%m%d}.zip"
    return FileResponse(
        tmp, media_type="application/zip", filename=filename,
        background=BackgroundTask(os.unlink, tmp),
    )


@router.post("/import")
def import_data(file: UploadFile):
    """Merge an uploaded .zip into the library (cp -rf) and rebuild the cache."""
    if recording.is_recording():
        raise HTTPException(status_code=409, detail="Stop the current recording first.")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out)
        result = archive.import_archive(Path(tmp))
    except archive.ArchiveError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        os.unlink(tmp)
    return {"added": result.added, "updated": result.updated}
```

Add `from pathlib import Path` and `import shutil` to the top of `songcoach/routes/api.py` if not already present (they are not — add them).

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS (new API tests + all existing tests green).

- [ ] **Step 5: Commit**

```bash
git add songcoach/routes/api.py tests/test_archive_api.py
git commit -m "feat(api): /api/export + /api/import endpoints with recording guard"
```

---

### Task 4: Front end (buttons, wiring, styles) + docs

**Files:**
- Modify: `songcoach/templates/index.html` (library header buttons + hidden input + overlay/toast nodes)
- Create: `songcoach/static/js/library.js`
- Modify: `songcoach/templates/base.html` OR `index.html` scripts block (load `library.js`)
- Modify: `songcoach/static/css/styles.css` (button + overlay + toast styles)
- Modify: `README.md` (a short "Move your library to another Mac" note + tick the roadmap)

**Interfaces:**
- Consumes: `GET /api/export`, `POST /api/import`, `GET /api/recordings/status` from Tasks 1–3.
- Produces: no code interface; a working UI.

- [ ] **Step 1: Add the buttons + overlay markup**

In `songcoach/templates/index.html`, replace the library section's opening label line:

```html
    <span class="tape__label">Library · {{ jobs|length }} recording{{ '' if jobs|length == 1 else 's' }}</span>
```

with a header row that includes the actions:

```html
    <div class="library__head">
      <span class="tape__label">Library · {{ jobs|length }} recording{{ '' if jobs|length == 1 else 's' }}</span>
      <div class="library__actions">
        <button id="export-btn" type="button" class="chip chip--ghost">⤓ Export</button>
        <button id="import-btn" type="button" class="chip chip--ghost">⤒ Import</button>
        <input id="import-file" type="file" accept=".zip" hidden />
      </div>
    </div>
```

Just before the closing `</main>` (after the `footnote` paragraph), add the overlay + toast:

```html
  <div id="io-overlay" class="io-overlay" hidden>
    <div class="io-overlay__card"><span class="io-spinner"></span><p id="io-overlay-msg">Importing… this can take a minute.</p></div>
  </div>
  <div id="io-toast" class="io-toast" role="status" hidden></div>
```

Change the scripts block at the bottom of `index.html` from:

```html
{% block scripts %}<script src="/static/js/app.js"></script>{% endblock %}
```

to:

```html
{% block scripts %}<script src="/static/js/app.js"></script><script src="/static/js/library.js"></script>{% endblock %}
```

- [ ] **Step 2: Write `library.js`**

Create `songcoach/static/js/library.js`:

```javascript
// Export / import the whole data/ library. Backend enforces the recording guard
// (409) — we disable Export on load as a courtesy and surface any 409 as a toast.
const exportBtn = document.getElementById("export-btn");
const importBtn = document.getElementById("import-btn");
const importFile = document.getElementById("import-file");
const overlay = document.getElementById("io-overlay");
const overlayMsg = document.getElementById("io-overlay-msg");
const toast = document.getElementById("io-toast");

function showToast(msg, isError = false) {
  toast.textContent = msg;
  toast.classList.toggle("io-toast--error", isError);
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 4000);
}

// Courtesy disable of Export while a capture is running.
(async () => {
  try {
    const { recording } = await (await fetch("/api/recordings/status")).json();
    if (recording) exportBtn.disabled = true;
  } catch {}
})();

exportBtn?.addEventListener("click", () => {
  // Native browser download (free progress bar); a 409 lands as a downloaded
  // error body, so re-check status first for a friendly message.
  fetch("/api/recordings/status")
    .then((r) => r.json())
    .then(({ recording }) => {
      if (recording) return showToast("Stop the current recording first.", true);
      window.location = "/api/export";
    })
    .catch(() => { window.location = "/api/export"; });
});

importBtn?.addEventListener("click", () => importFile.click());

importFile?.addEventListener("change", async () => {
  const file = importFile.files[0];
  importFile.value = ""; // allow re-picking the same file later
  if (!file) return;
  if (!confirm("Merge these recordings into your library? Any with the same ID will be overwritten.")) return;

  overlayMsg.textContent = "Importing… this can take a minute.";
  overlay.hidden = false;
  try {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/import", { method: "POST", body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.detail || "Import failed.", true);
      return;
    }
    const total = (data.added || 0) + (data.updated || 0);
    showToast(`Imported ${total} recording${total === 1 ? "" : "s"}.`);
    setTimeout(() => window.location.reload(), 900);
  } catch (err) {
    showToast("Import failed: " + err.message, true);
  } finally {
    overlay.hidden = true;
  }
});
```

- [ ] **Step 3: Add styles**

Append to `songcoach/static/css/styles.css`:

```css
/* Library export/import */
.library__head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.library__actions { display: flex; gap: 0.5rem; }

.io-overlay {
  position: fixed; inset: 0; z-index: 50;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}
.io-overlay__card {
  display: flex; flex-direction: column; align-items: center; gap: 0.9rem;
  padding: 1.6rem 2rem; border-radius: 12px;
  background: #1a1a1d; color: #f2f2f2; text-align: center;
}
.io-spinner {
  width: 26px; height: 26px; border-radius: 50%;
  border: 3px solid rgba(255, 255, 255, 0.25); border-top-color: #fff;
  animation: io-spin 0.8s linear infinite;
}
@keyframes io-spin { to { transform: rotate(360deg); } }

.io-toast {
  position: fixed; left: 50%; bottom: 1.5rem; transform: translateX(-50%);
  z-index: 60; padding: 0.7rem 1.1rem; border-radius: 10px;
  background: #1f6f43; color: #fff; font-size: 0.95rem;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.3);
}
.io-toast--error { background: #8a2d2d; }
```

- [ ] **Step 4: Verify in the browser (Playwright)**

Start a dev server against a copy of the real data (or the existing `./data`):

```bash
.venv/bin/python -m uvicorn songcoach.main:app --port 8137 &
```

Then, using the project `playwright` MCP tools:
1. `browser_navigate` to `http://127.0.0.1:8137/`.
2. `browser_snapshot` — confirm **Export** and **Import** buttons render in the library header.
3. Click **Export** (`browser_click`) — confirm a network request to `/api/export` returns 200 (`browser_network_requests`) and a `.zip` downloads.
4. Click **Import** (`browser_click`), then `browser_file_upload` with the zip exported in step 3. Accept the confirm (`browser_handle_dialog`). Confirm the overlay appears, then a toast "Imported N recordings", then the page reloads with the library intact.
5. Screenshot lands in the project root; eyeball the buttons + toast.

Stop the server (`kill %1`). Expected: buttons present, export downloads a zip, import round-trips and reloads.

- [ ] **Step 5: Update README + roadmap**

In `README.md`, under the **Using it** section (after the player bullet list, before **How it works**), add:

```markdown
### Move your library to another Mac

Your whole library lives in one folder, so moving it is two clicks. On the old
Mac, hit **Export** to download a `SongCoach-export-….zip`. Copy it over
(AirDrop, USB, wherever), then on the new Mac hit **Import** and pick the zip —
your recordings merge in (anything with the same ID is overwritten) and the
library reloads. Nothing leaves your machines but the file you carry.
```

In the **Roadmap** section, change:

```markdown
- ⬜ Delete/cleanup recordings from the library
```

to add an export/import line right above it:

```markdown
- ✅ Export / import your library as a `.zip` (move it between Macs)
- ⬜ Delete/cleanup recordings from the library
```

- [ ] **Step 6: Commit**

```bash
git add songcoach/templates/index.html songcoach/static/js/library.js songcoach/static/css/styles.css README.md
git commit -m "feat(ui): Export/Import buttons for the data library + docs"
```

---

## Self-Review

**Spec coverage:**
- Archive format + manifest → Task 1 (`build_export`, manifest, `ZIP_STORED`). ✓
- `cp -rf` overlay + zip-slip + whitelist + rebuild + counts → Task 2. ✓
- Edge case (archive missing a stem the local job has) → covered by `test_import_cp_rf_overwrites_conflict_keeps_others` (local `drums.mp3` kept). ✓
- `GET /api/export` / `POST /api/import`, 409 guard, 422 → Task 3. ✓
- UI buttons, confirm, overlay, toast, reload, courtesy-disable Export → Task 4. ✓
- README note + roadmap tick → Task 4 Step 5. ✓
- Tests enumerated in the spec → all present across Tasks 1–3. ✓

**Placeholder scan:** No TBD/TODO; every code step has concrete code. ✓

**Type consistency:** `ImportResult(added, updated)`, `ArchiveError`, `MANIFEST_NAME`, `build_export(dest_zip)->int`, `import_archive(zip_path)->ImportResult` used identically across Tasks 1–3. Route returns `{"added","updated"}` matching `library.js`'s `data.added`/`data.updated`. ✓

**Note for the implementer:** `routes/api.py` currently imports `from pathlib import Path`? It does NOT — add `from pathlib import Path` and `import shutil` (Task 3 Step 3 calls this out). `os`, `tempfile`, `date`, `UploadFile`, `FileResponse`, `BackgroundTask`, `archive` are also new imports listed in Task 3.
