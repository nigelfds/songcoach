# Waveform Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user annotate a recording's waveforms with named markers — a vertical line + "i" badge spanning all three synced rows at a timestamp — persisted in the `meta.json` sidecar.

**Architecture:** Markers live only in the sidecar (like the soft-delete `deleted` flag); `GET`/`PUT /api/jobs/{id}/markers` read/write them. The player renders each marker as an overlay in `#strips` reusing the existing `phGeom` time→pixel geometry, edits them via a modal (the `.edit-overlay` idiom), and places new ones via a "marker mode" capture overlay with a live time tooltip.

**Tech Stack:** FastAPI + pydantic, pytest, Jinja2, vanilla JS (WaveSurfer.js v7), CSS. Frontend verified via Playwright (no JS unit runner).

## Global Constraints

- **Run tests with** `.venv/bin/python -m pytest` (NOT `.venv/bin/pytest`).
- **Markers are sidecar-only** — no `Job`/DB column. `meta.json` key `markers` = `[{id: str, time: number≥0, name: str}]`.
- **`write_meta` must preserve `markers`** (and the existing `deleted`) — an edit/thumbnail-refresh write must never wipe them.
- Validation on PUT: list capped at **200**; each `time ≥ 0`, `name` trimmed + capped **120** chars; bad payload → **422**; no sidecar → **404**.
- **Marker geometry reuses `phGeom`** (`x = phGeom.left + time/duration × phGeom.width`, `top = phGeom.top`, `height = phGeom.height`) so a marker spans all rows incl. REF.
- `fmt1(sec)` (already in player.js) formats `mm:ss.d` — use it for the timestamp + tooltip.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Backend — markers in the sidecar + endpoints

**Files:**
- Modify: `songcoach/metadata.py` (`read_markers`, `write_markers`; preserve `markers` in `write_meta`)
- Modify: `songcoach/routes/api.py` (GET/PUT `/jobs/{id}/markers`)
- Test: `tests/test_markers.py`

**Interfaces:**
- Consumes: `metadata.meta_path`, `metadata.read_meta`, `metadata.write_meta`, `metadata.job_dir`, `metadata.write_meta`'s existing atomic pattern; `SessionLocal`, `Job`.
- Produces:
  - `metadata.read_markers(job_id) -> list`
  - `metadata.write_markers(job_id, markers: list) -> bool`
  - `GET /api/jobs/{id}/markers` → `{"markers": [...]}`, `PUT` → `{"markers": [...]}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_markers.py
import json

import pytest
from fastapi.testclient import TestClient

from songcoach import metadata
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


def _seed(jid="m1"):
    s = SessionLocal()
    try:
        job = Job(id=jid, title="Song", artist="Artist", status=JobStatus.done)
        s.add(job)
        s.commit()
        metadata.write_meta(job)   # creates the sidecar
    finally:
        s.close()
    return jid


def _sidecar(jid):
    return json.loads(metadata.meta_path(jid).read_text(encoding="utf-8"))


# --- metadata helpers ------------------------------------------------------

def test_read_markers_empty_when_none(storage_dir):
    _seed("a")
    assert metadata.read_markers("a") == []


def test_write_then_read_markers(storage_dir):
    _seed("b")
    ms = [{"id": "x", "time": 12.5, "name": "Solo"}]
    assert metadata.write_markers("b", ms) is True
    assert metadata.read_markers("b") == ms
    assert _sidecar("b")["title"] == "Song"   # other keys intact


def test_write_markers_missing_sidecar(storage_dir):
    assert metadata.write_markers("ghost", []) is False


def test_write_meta_preserves_markers(storage_dir):
    jid = _seed("c")
    metadata.write_markers(jid, [{"id": "x", "time": 1.0, "name": "A"}])
    s = SessionLocal()
    try:
        metadata.write_meta(s.get(Job, jid))   # a later edit must not wipe markers
    finally:
        s.close()
    assert metadata.read_markers(jid) == [{"id": "x", "time": 1.0, "name": "A"}]


# --- endpoints -------------------------------------------------------------

@pytest.fixture
def client(storage_dir):
    from songcoach.main import app
    return TestClient(app)


def test_get_markers(client, storage_dir):
    _seed("g")
    r = client.get("/api/jobs/g/markers")
    assert r.status_code == 200 and r.json() == {"markers": []}


def test_get_markers_404_no_sidecar(client, storage_dir):
    assert client.get("/api/jobs/nope/markers").status_code == 404


def test_put_markers_ok(client, storage_dir):
    _seed("p")
    body = {"markers": [{"id": "1", "time": 30.2, "name": "  Fill  "}]}
    r = client.put("/api/jobs/p/markers", json=body)
    assert r.status_code == 200
    assert r.json()["markers"][0]["name"] == "Fill"          # trimmed
    assert metadata.read_markers("p")[0]["time"] == 30.2      # persisted


def test_put_markers_404_no_sidecar(client, storage_dir):
    r = client.put("/api/jobs/nope/markers", json={"markers": []})
    assert r.status_code == 404


def test_put_markers_422_bad_payload(client, storage_dir):
    _seed("q")
    assert client.put("/api/jobs/q/markers", json={"markers": "nope"}).status_code == 422
    assert client.put("/api/jobs/q/markers",
                      json={"markers": [{"id": "1", "time": -5, "name": "x"}]}).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_markers.py -v`
Expected: FAIL — `AttributeError: module 'songcoach.metadata' has no attribute 'read_markers'` / 405 on the routes.

- [ ] **Step 3: Add the metadata helpers + preserve markers**

In `songcoach/metadata.py`, add after `mark_deleted` (`json`, `Path` already imported):

```python
def read_markers(job_id: str) -> list:
    """The job's markers from its sidecar (``[]`` if none / unreadable)."""
    path = meta_path(job_id)
    if not path.exists():
        return []
    try:
        return read_meta(path).get("markers") or []
    except (ValueError, OSError):
        return []


def write_markers(job_id: str, markers: list) -> bool:
    """Store the markers array in the sidecar, atomically, preserving other keys.

    Returns ``False`` if there is no sidecar.
    """
    path = meta_path(job_id)
    if not path.exists():
        return False
    data = read_meta(path)
    data["markers"] = markers
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True
```

Then generalize `write_meta`'s preserve step. It currently carries the `deleted`
flag; change that block to carry a set of sidecar-only keys. Replace:

```python
    if path.exists():
        try:
            if read_meta(path).get("deleted"):
                data["deleted"] = True
        except (ValueError, OSError):
            pass
```

with:

```python
    if path.exists():
        try:
            existing = read_meta(path)
            for key in ("deleted", "markers"):
                if key in existing:
                    data[key] = existing[key]
        except (ValueError, OSError):
            pass
```

- [ ] **Step 4: Add the endpoints**

In `songcoach/routes/api.py`, extend the pydantic import — change
`from pydantic import BaseModel` to `from pydantic import BaseModel, Field`.

Add the request models near the other `*In` models:

```python
class MarkerIn(BaseModel):
    id: str = Field(max_length=64)
    time: float = Field(ge=0)
    name: str = Field(default="", max_length=120)


class MarkersIn(BaseModel):
    markers: list[MarkerIn] = Field(max_length=200)
```

Add the endpoints (near the other `/jobs/{job_id}` routes):

```python
@router.get("/jobs/{job_id}/markers")
def get_markers(job_id: str):
    if not metadata.meta_path(job_id).exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"markers": metadata.read_markers(job_id)}


@router.put("/jobs/{job_id}/markers")
def put_markers(job_id: str, payload: MarkersIn):
    markers = []
    for m in payload.markers:
        markers.append({"id": m.id, "time": m.time, "name": m.name.strip()})
    if not metadata.write_markers(job_id, markers):
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"markers": markers}
```

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_markers.py -v`  → PASS (all).
Run: `.venv/bin/python -m pytest -q`  → whole suite green (82 prior + new).

- [ ] **Step 6: Commit**

```bash
git add songcoach/metadata.py songcoach/routes/api.py tests/test_markers.py
git commit -m "feat(markers): sidecar markers storage + GET/PUT endpoints"
```

---

### Task 2: Player — render, edit & delete markers

**Files:**
- Modify: `songcoach/templates/player.html` (marker layer node + marker modal)
- Modify: `songcoach/static/js/player.js` (load, render, layout, modal, persist)
- Modify: `songcoach/static/css/styles.css` (marker line + "i" badge styles)

**Interfaces:**
- Consumes: `GET`/`PUT /api/jobs/{id}/markers` (Task 1); existing `phGeom`, `duration()`, `fmt1()`, `layoutPlayhead()`, `onAllReady()`, the `resize` handler, the keydown handler, the `.edit-overlay` modal idiom.
- Produces (used by Task 3): `openMarker(m, isNew)`, `renderMarkers()`, `layoutMarkers()`, the module-level `markers` array.

- [ ] **Step 1: Add the marker layer + modal markup**

In `songcoach/templates/player.html`, inside `#strips`, right after the playhead
line `<div id="playhead" class="playhead" hidden></div>`, add:

```html
        <div id="marker-layer" class="marker-layer"></div>
```

After the keyboard-shortcuts overlay (`#help-overlay` … its closing `</div>`),
add the marker modal:

```html
  <!-- MARKER -->
  <div id="marker-overlay" class="edit-overlay" hidden>
    <div class="edit-card" role="dialog" aria-modal="true" aria-label="Marker">
      <div class="rack-screws"><i></i><i></i><i></i><i></i></div>
      <span class="tape__label">Marker · <span id="marker-time">0:00.0</span></span>
      <div class="meta">
        <input id="marker-name" class="meta__in" type="text" maxlength="120"
               autocomplete="off" spellcheck="false"
               placeholder="Marker name (e.g. Guitar solo)" />
      </div>
      <p class="jack__error" id="marker-error" role="alert"></p>
      <div class="edit-actions">
        <button id="marker-delete" class="chip chip--ghost" type="button">DELETE</button>
        <button id="marker-cancel" class="chip chip--ghost" type="button">CANCEL</button>
        <button id="marker-save" class="btn-primary" type="button">SAVE</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Add CSS**

Append to `songcoach/static/css/styles.css`:

```css
/* Waveform markers */
.marker-layer { position: absolute; inset: 0; pointer-events: none; z-index: 4; }
.marker { position: absolute; width: 0; border-left: 2px dashed var(--amber); }
.marker__i {
  position: absolute; top: -9px; left: -9px; width: 18px; height: 18px;
  display: grid; place-items: center; border-radius: 50%;
  font: italic 700 11px/1 var(--font-body, sans-serif);
  background: var(--amber); color: #1a1a1d; border: none;
  cursor: pointer; pointer-events: auto;
}
.marker__i:hover { filter: brightness(1.08); }
```

- [ ] **Step 3: Add the JS (load / render / layout / modal / persist)**

In `songcoach/static/js/player.js`:

**(a)** Near the other module-level state (e.g. by `let phGeom = null;`), add:

```javascript
let markers = [];               // [{id, time, name}]
let editingMarkerId = null;
let editingIsNew = false;
```

**(b)** Add these functions (put them just above the edit-modal section near the
bottom of the file):

```javascript
const markerLayer = document.getElementById("marker-layer");
const markerOverlay = document.getElementById("marker-overlay");
const markerName = document.getElementById("marker-name");
const markerTimeEl = document.getElementById("marker-time");
const markerError = document.getElementById("marker-error");

function markerX(t) {
  const dur = duration() || 1;
  return phGeom.left + (Math.min(Math.max(0, t), dur) / dur) * phGeom.width;
}

function renderMarkers() {
  if (!markerLayer) return;
  markerLayer.replaceChildren();
  if (!phGeom) return;
  markers.forEach((m) => {
    const el = document.createElement("div");
    el.className = "marker";
    el.dataset.id = m.id;
    el.style.left = markerX(m.time) + "px";
    el.style.top = phGeom.top + "px";
    el.style.height = phGeom.height + "px";
    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "marker__i";
    badge.textContent = "i";
    badge.title = m.name || "Marker";
    badge.setAttribute("aria-label", "Marker: " + (m.name || "unnamed"));
    badge.addEventListener("click", (e) => { e.stopPropagation(); openMarker(m, false); });
    el.appendChild(badge);
    markerLayer.appendChild(el);
  });
}

function layoutMarkers() { if (phGeom) renderMarkers(); }

async function loadMarkers() {
  try {
    const res = await fetch(`/api/jobs/${jobId}/markers`);
    if (res.ok) { markers = (await res.json()).markers || []; renderMarkers(); }
  } catch {}
}

async function persistMarkers() {
  try {
    const res = await fetch(`/api/jobs/${jobId}/markers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markers }),
    });
    if (res.ok) { markers = (await res.json()).markers || markers; return true; }
  } catch {}
  return false;
}

function openMarker(m, isNew) {
  editingMarkerId = m.id;
  editingIsNew = isNew;
  markerTimeEl.textContent = fmt1(m.time);
  markerName.value = m.name || "";
  markerError.textContent = "";
  markerOverlay.hidden = false;
  markerName.focus();
}

function closeMarker() {
  // Cancelling a brand-new, never-saved marker discards it.
  if (editingIsNew) {
    markers = markers.filter((x) => x.id !== editingMarkerId);
    renderMarkers();
  }
  markerOverlay.hidden = true;
  editingMarkerId = null;
}

async function saveMarker() {
  const m = markers.find((x) => x.id === editingMarkerId);
  if (!m) { markerOverlay.hidden = true; editingMarkerId = null; return; }
  const prev = m.name;
  m.name = markerName.value.trim();
  if (!(await persistMarkers())) {
    m.name = prev;
    markerError.textContent = "Couldn't save. Try again.";
    return;
  }
  editingIsNew = false;
  renderMarkers();
  markerOverlay.hidden = true;
  editingMarkerId = null;
}

async function deleteMarker() {
  const keep = markers.filter((x) => x.id !== editingMarkerId);
  const prev = markers;
  markers = keep;
  if (!(await persistMarkers())) {
    markers = prev;
    markerError.textContent = "Couldn't delete. Try again.";
    return;
  }
  editingIsNew = false;
  renderMarkers();
  markerOverlay.hidden = true;
  editingMarkerId = null;
}

document.getElementById("marker-save").addEventListener("click", saveMarker);
document.getElementById("marker-cancel").addEventListener("click", closeMarker);
document.getElementById("marker-delete").addEventListener("click", deleteMarker);
markerOverlay.addEventListener("click", (e) => { if (e.target === markerOverlay) closeMarker(); });
```

**(c)** Load markers once the deck is ready. In `onAllReady()`, right after the
`layoutPlayhead();` call, add:

```javascript
  loadMarkers();
```

**(d)** Keep markers aligned on resize. The resize handler is:

```javascript
window.addEventListener("resize", () => { if (phGeom) layoutPlayhead(); });
```

change it to:

```javascript
window.addEventListener("resize", () => { if (phGeom) { layoutPlayhead(); layoutMarkers(); } });
```

**(e)** Escape closes the marker modal (and typing in it doesn't trigger transport
shortcuts). In the keydown handler, next to the existing
`if (!overlay.hidden) { if (e.key === "Escape") closeEditor(); return; }` lines,
add (before them):

```javascript
  if (!markerOverlay.hidden) { if (e.key === "Escape") closeMarker(); return; }
```

- [ ] **Step 4: Verify (implementer: suite + render smoke-check; controller: Playwright)**

Do NOT run Playwright — the controller runs the browser acceptance. Implementer
verification:

1. `.venv/bin/python -m pytest -q` → whole suite still green.
2. Render smoke-check with a **seeded marker** (so rendering has something to show)
   in an isolated data dir:
```
ROOT=/tmp/mk; rm -rf "$ROOT"; mkdir -p "$ROOT/data/jobs/j"
d="$ROOT/data/jobs/j"; for t in original drums no_drums; do printf audio > "$d/$t.mp3"; done
cat > "$d/meta.json" <<'JSON'
{"schema_version":1,"id":"j","title":"T","artist":"A","youtube_url":null,"duration_seconds":120.0,"status":"done","error":null,"created_at":"2026-08-01T12:00:00+00:00","updated_at":"2026-08-01T12:00:00+00:00","tracks":[{"kind":"original","file":"original.mp3","duration_seconds":120.0},{"kind":"drums","file":"drums.mp3","duration_seconds":120.0},{"kind":"no_drums","file":"no_drums.mp3","duration_seconds":120.0}],"markers":[{"id":"x","time":30.0,"name":"Solo"}]}
JSON
LOCAL_STORAGE_DIR=$ROOT/data DATABASE_URL=sqlite:///$ROOT/m.db .venv/bin/python -m uvicorn songcoach.main:app --port 8152 >/tmp/mk.log 2>&1 &
SRV=$!; sleep 4
curl -s http://127.0.0.1:8152/jobs/j | grep -o 'id="marker-\(layer\|overlay\|name\|save\|delete\)"' | sort -u
curl -s http://127.0.0.1:8152/api/jobs/j/markers
kill $SRV; rm -rf "$ROOT"
```
Expect: the marker layer + modal ids present; `/markers` returns the seeded Solo marker.

**Controller Playwright acceptance** (isolated dir, seeded marker as above):
- Open the player; wait for the deck; assert a `.marker` line renders with an
  `.marker__i` badge, and its height spans the full strip stack (≈ playhead height).
- Click the badge → modal shows `Marker · 0:30.0` and name "Solo".
- Change the name → Save → `PUT` fires; reload → the marker persists with the new
  name (proves the sidecar round-trip).
- Click badge → Delete → marker gone; reload → still gone.

- [ ] **Step 5: Commit**

```bash
git add songcoach/templates/player.html songcoach/static/js/player.js songcoach/static/css/styles.css
git commit -m "feat(player): render, edit & delete waveform markers"
```

---

### Task 3: Player — marker mode, click-to-place & time tooltip

**Files:**
- Modify: `songcoach/templates/player.html` (marker icon, capture overlay, tooltip node)
- Modify: `songcoach/static/js/player.js` (mode toggle, placement, tooltip)
- Modify: `songcoach/static/css/styles.css` (capture, tooltip, lit icon)

**Interfaces:**
- Consumes: Task 2's `openMarker(m, isNew)`, `renderMarkers()`, `markers`, `layoutMarkers`; existing `phGeom`, `duration()`, `fmt1()`, `#strips`.

- [ ] **Step 1: Add the marker icon + capture overlay + tooltip markup**

In `songcoach/templates/player.html`, add the marker icon to `.console__controls`,
right after the `#edit-open` button:

```html
        <button id="marker-open" class="icon-btn" type="button" title="Add markers" aria-label="Add markers" aria-pressed="false"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 21V4h11l-2.5 4L17 12H6"/></svg></button>
```

Inside `#strips`, right after the `#marker-layer` div (added in Task 2), add:

```html
        <div id="marker-capture" class="marker-capture" hidden></div>
        <div id="marker-tip" class="marker-tip" hidden></div>
```

- [ ] **Step 2: Add CSS**

Append to `songcoach/static/css/styles.css`:

```css
.marker-capture { position: absolute; z-index: 5; cursor: crosshair; }
.marker-tip {
  position: absolute; z-index: 6; transform: translate(-50%, -100%);
  padding: 2px 6px; border-radius: 6px; font-size: 11px; white-space: nowrap;
  background: #1a1a1d; color: #fff; pointer-events: none;
}
.icon-btn.is-on { border-color: var(--amber); color: var(--amber); }
```

- [ ] **Step 3: Add the JS (mode toggle, placement, tooltip)**

In `songcoach/static/js/player.js`:

**(a)** Near the other marker state, add:

```javascript
let markerMode = false;
```

**(b)** Add refs + logic (below the Task-2 marker functions):

```javascript
const markerOpenBtn = document.getElementById("marker-open");
const markerCapture = document.getElementById("marker-capture");
const markerTip = document.getElementById("marker-tip");

function positionCapture() {
  if (!phGeom || !markerMode) return;
  markerCapture.style.left = phGeom.left + "px";
  markerCapture.style.top = phGeom.top + "px";
  markerCapture.style.width = phGeom.width + "px";
  markerCapture.style.height = phGeom.height + "px";
}

function setMarkerMode(on) {
  markerMode = on;
  markerOpenBtn.classList.toggle("is-on", on);
  markerOpenBtn.setAttribute("aria-pressed", String(on));
  markerCapture.hidden = !on;
  if (on) positionCapture();
  else markerTip.hidden = true;
}

function timeAtClientX(clientX) {
  const base = document.getElementById("strips").getBoundingClientRect();
  const x = clientX - base.left - phGeom.left;
  const dur = duration() || 1;
  return Math.min(Math.max(0, (x / phGeom.width) * dur), dur);
}

markerOpenBtn.addEventListener("click", () => setMarkerMode(!markerMode));

markerCapture.addEventListener("click", (e) => {
  if (!phGeom) return;
  const m = { id: crypto.randomUUID(), time: timeAtClientX(e.clientX), name: "" };
  markers.push(m);
  renderMarkers();
  markerTip.hidden = true;
  openMarker(m, true);
});

markerCapture.addEventListener("mousemove", (e) => {
  if (!phGeom) return;
  const base = document.getElementById("strips").getBoundingClientRect();
  markerTip.textContent = fmt1(timeAtClientX(e.clientX));
  markerTip.style.left = (e.clientX - base.left) + "px";
  markerTip.style.top = (e.clientY - base.top - 10) + "px";
  markerTip.hidden = false;
});

markerCapture.addEventListener("mouseleave", () => { markerTip.hidden = true; });
```

**(c)** Keep the capture overlay aligned on resize. `layoutMarkers()` (from Task 2)
is now called in the resize handler; extend it to also reposition the capture
overlay. Change Task 2's:

```javascript
function layoutMarkers() { if (phGeom) renderMarkers(); }
```

to:

```javascript
function layoutMarkers() { if (phGeom) { renderMarkers(); positionCapture(); } }
```

(`positionCapture` no-ops unless marker mode is on.)

- [ ] **Step 4: Verify (implementer: suite + smoke-check; controller: Playwright)**

Do NOT run Playwright. Implementer verification:

1. `.venv/bin/python -m pytest -q` → whole suite green.
2. Smoke-check the markup renders (reuse the Task-2 seeded-job server on port 8153):
```
curl -s http://127.0.0.1:8153/jobs/j | grep -o 'id="marker-\(open\|capture\|tip\)"' | sort -u
```
Expect all three ids present.

**Controller Playwright acceptance** (isolated dir with a seeded done job, no
markers):
- Click `#marker-open` → it gets `is-on` / `aria-pressed="true"`, `#marker-capture`
  becomes visible.
- Dispatch a `mousemove` over the capture overlay → `#marker-tip` shows a `mm:ss.d`
  time and follows x.
- Click the capture overlay at a known x → a new `.marker` appears at ~that time
  and the modal opens (timestamp shown, empty name); type a name, Save → it
  persists (reload shows it).
- Toggle `#marker-open` off → capture hidden, tooltip hidden, `is-on` removed;
  waveform click seeks again (marker mode is sticky until toggled).

- [ ] **Step 5: Commit**

```bash
git add songcoach/templates/player.html songcoach/static/js/player.js songcoach/static/css/styles.css
git commit -m "feat(player): marker mode — click-to-place with live time tooltip"
```

---

## Self-Review

**Spec coverage:**
- Markers in `meta.json` (`{id,time,name}`), sidecar-only; `write_meta` preserves → Task 1. ✓
- GET/PUT endpoints + validation (422/404, trim, caps) → Task 1. ✓
- Marker icon next to help/edit → Task 3 Step 1. ✓
- Line spans all rows incl. REF via `phGeom` → Task 2 (`renderMarkers`, `top`/`height`). ✓
- "i" badge; name only shown in modal → Task 2. ✓
- Modal (timestamp + name + delete) reused for place & edit → Task 2 (`openMarker`) + Task 3 (calls with `isNew`). ✓
- Marker mode + click-to-place, seek/loop untouched (capture overlay) → Task 3. ✓
- Live time tooltip following the cursor in marker mode → Task 3. ✓
- Persist across reload → Task 2 (`persistMarkers` + `loadMarkers`). ✓
- Tests (backend round-trip/validation/write_meta-preserve; Playwright render/place/edit/delete) → all three tasks. ✓

**Placeholder scan:** No TBD/TODO; complete code in every step.

**Type/name consistency:** `markers`/`openMarker(m,isNew)`/`renderMarkers`/`layoutMarkers`/`persistMarkers`/`markerX`/`timeAtClientX`/`positionCapture` consistent across Tasks 2–3. Ids `#marker-layer/-overlay/-name/-time/-error/-save/-cancel/-delete/-open/-capture/-tip` match template ↔ JS. `metadata.read_markers`/`write_markers`, `MarkerIn`/`MarkersIn`, `GET`/`PUT /api/jobs/{id}/markers` consistent in Task 1 + its test.

**Notes for implementers:**
- `layoutMarkers` is introduced in Task 2 and *extended* in Task 3 (adds `positionCapture()`); Task 3 Step 3(c) shows the exact before/after.
- The capture overlay (Task 3) sits at `z-index:5`, above the marker layer (`z-index:4`), so while marker mode is on the "i" badges aren't clickable — intended (you edit existing markers with the mode off).
- `crypto.randomUUID()` is available in the app's browser context (secure context / localhost); it is only forbidden in Workflow scripts, not player JS.
