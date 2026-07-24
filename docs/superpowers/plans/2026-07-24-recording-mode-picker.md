# Recording Mode Picker Implementation Plan

> **For agentic workers:** Executed inline (single-agent) via superpowers:executing-plans with TDD. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the landing page with a two-card picker (Record from YouTube / Record from system audio); system mode adds a manual-capture flow with a user-supplied image-URL thumbnail.

**Architecture:** Front-end restructure of the landing page into three client-toggled views sharing one metadata+capture block, plus a small backend addition to fetch an arbitrary image URL and store it as the job thumbnail (fetch-and-forget, no schema change).

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), Jinja2 + vanilla JS, inline SVG icons, pytest + httpx.

## Global Constraints

- Python 3.11; run tests with `.venv/bin/python -m pytest` (NOT `.venv/bin/pytest`).
- Front-end stays self-contained: inline SVG icons, no external assets/CDNs.
- Icons are custom, **grayscale**, trademark-safe (reminiscent, not copies), matching the existing analog-gear aesthetic (`.rack-screws`, `.tape`, `.chip`, `.btn-rec`).
- **Option A** thumbnail handling: fetch the image once at capture start, store `thumbnail.jpg`, do NOT persist the URL. No DB/model/sidecar/schema change.
- Back is disabled while a capture is in progress.
- Tests MUST NOT hit the network or run real `syscap`/Demucs (monkeypatch).
- Commit after each task; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Image-thumbnail helpers in `fetch_thumbnails.py`

**Files:**
- Modify: `songcoach/fetch_thumbnails.py` (add helpers after `fetch_thumbnail`, ~line 52)
- Test: `tests/test_image_thumbnail.py`

**Interfaces:**
- Produces: `_download_image(url, max_bytes=5*1024*1024) -> bytes | None`;
  `store_image_from_url(job_id, image_url) -> None`;
  `store_image_from_url_async(job_id, image_url) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_image_thumbnail.py`:

```python
from songcoach import fetch_thumbnails as ft
from songcoach.metadata import thumbnail_path


class _FakeHeaders:
    def __init__(self, ct):
        self._ct = ct

    def get_content_type(self):
        return self._ct


class _FakeResp:
    def __init__(self, data=b"", status=200, content_type="image/jpeg"):
        self._data = data
        self.status = status
        self.headers = _FakeHeaders(content_type)

    def read(self, n=-1):
        return self._data if (n is None or n < 0) else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_image_rejects_non_image(monkeypatch):
    monkeypatch.setattr(ft, "urlopen", lambda *a, **k: _FakeResp(b"<html>", content_type="text/html"))
    assert ft._download_image("http://x/y") is None


def test_download_image_rejects_oversize(monkeypatch):
    monkeypatch.setattr(ft, "urlopen", lambda *a, **k: _FakeResp(b"x" * 50, content_type="image/png"))
    assert ft._download_image("http://x/y", max_bytes=10) is None


def test_download_image_returns_bytes(monkeypatch):
    monkeypatch.setattr(ft, "urlopen", lambda *a, **k: _FakeResp(b"IMG", content_type="image/jpeg"))
    assert ft._download_image("http://x/y") == b"IMG"


def test_store_image_writes_thumbnail(monkeypatch, storage_dir):
    monkeypatch.setattr(ft, "_download_image", lambda url, **k: b"IMG")
    ft.store_image_from_url("job42", "http://x/y")
    p = thumbnail_path("job42")
    assert p.exists() and p.read_bytes() == b"IMG"


def test_store_image_noop_when_download_fails(monkeypatch, storage_dir):
    monkeypatch.setattr(ft, "_download_image", lambda url, **k: None)
    ft.store_image_from_url("job43", "http://x/y")
    assert not thumbnail_path("job43").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_image_thumbnail.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_download_image'`.

- [ ] **Step 3: Implement the helpers**

In `songcoach/fetch_thumbnails.py`, add after `fetch_thumbnail` (`thumbnail_path` is already imported at line 24):

```python
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB cap for user-supplied thumbnails


def _download_image(url: str, max_bytes: int = _MAX_IMAGE_BYTES) -> bytes | None:
    """Download a user-supplied image URL, bounded by content-type + size."""
    req = Request(url, headers={"User-Agent": "SongCoach/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return None
            ctype = resp.headers.get_content_type()
            if not ctype.startswith("image/"):
                log.warning("Not an image (%s): %s", ctype, url)
                return None
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                log.warning("Image too large (> %d bytes): %s", max_bytes, url)
                return None
            return data
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def store_image_from_url(job_id: str, image_url: str) -> None:
    """Fetch an image URL and store it as the job's thumbnail (best-effort).

    Writes only the image file — not the sidecar; the job is still ``recording``
    here, and ``meta.json`` gets written later (process success / _fail), with
    ``to_dict`` picking up the thumbnail when the file exists.
    """
    data = _download_image(image_url)
    if not data:
        return
    dest = thumbnail_path(job_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    log.info("Stored thumbnail for %s from %s (%d KB)", job_id, image_url, len(data) // 1024)


def store_image_from_url_async(job_id: str, image_url: str) -> None:
    threading.Thread(target=store_image_from_url, args=(job_id, image_url), daemon=True).start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_image_thumbnail.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add songcoach/fetch_thumbnails.py tests/test_image_thumbnail.py
git commit -m "feat: fetch + store a user-supplied image URL as job thumbnail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire `image_url` into `POST /recordings/start`

**Files:**
- Modify: `songcoach/routes/api.py` (`StartRecordingIn` ~line 27-30; `start_recording` ~line 77-90)
- Test: `tests/test_start_image.py`

**Interfaces:**
- Consumes: `fetch_thumbnails.store_image_from_url_async` (Task 1).
- Produces: `StartRecordingIn.image_url: str | None`; `start_recording` fires the async store when a non-blank `image_url` is present.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_start_image.py`:

```python
import pytest
from fastapi.testclient import TestClient

from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus


@pytest.fixture
def client(storage_dir, monkeypatch):
    calls = []
    from songcoach import fetch_thumbnails, recording

    def fake_start(**kw):
        s = SessionLocal()
        job = Job(title=kw.get("title"), artist=kw.get("artist"),
                  youtube_url=kw.get("youtube_url"), status=JobStatus.recording)
        s.add(job)
        s.commit()
        jid = job.id
        s.close()
        return jid

    monkeypatch.setattr(recording, "start", fake_start)
    monkeypatch.setattr(fetch_thumbnails, "store_image_from_url_async",
                        lambda jid, url: calls.append((jid, url)))
    from songcoach.main import app
    c = TestClient(app)
    c.image_calls = calls
    return c


def test_start_with_image_url_fires_store(client):
    r = client.post("/api/recordings/start", json={"title": "T", "image_url": "http://x/pic.jpg"})
    assert r.status_code == 201
    assert len(client.image_calls) == 1
    assert client.image_calls[0][1] == "http://x/pic.jpg"


def test_start_without_image_url_no_store(client):
    r = client.post("/api/recordings/start", json={"title": "T"})
    assert r.status_code == 201
    assert client.image_calls == []


def test_start_blank_image_url_no_store(client):
    r = client.post("/api/recordings/start", json={"title": "T", "image_url": "   "})
    assert r.status_code == 201
    assert client.image_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_start_image.py -v`
Expected: FAIL — `store_image_from_url_async` never called (field/wiring absent).

- [ ] **Step 3: Add the field and wiring**

In `songcoach/routes/api.py`, add to `StartRecordingIn`:

```python
class StartRecordingIn(BaseModel):
    title: str
    artist: str | None = None
    youtube_url: str | None = None
    image_url: str | None = None
```

In `start_recording`, after `job_id = recording.start(...)` succeeds and before `return`:

```python
    image_url = (payload.image_url or "").strip()
    if image_url:
        fetch_thumbnails.store_image_from_url_async(job_id, image_url)
```

(`fetch_thumbnails` is already imported at the top of `api.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_start_image.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add songcoach/routes/api.py tests/test_start_image.py
git commit -m "feat: accept image_url on recordings/start and store it as thumbnail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Landing-page mode picker (HTML + JS + CSS + icons)

**Files:**
- Modify: `songcoach/templates/index.html` (restructure `.hero__panel` into 3 views)
- Modify: `songcoach/static/js/app.js` (mode state machine, back, per-mode `begin()`)
- Modify: the landing CSS file under `songcoach/static/css/` (mode cards, icon collage, back button) — locate the exact file first.

**No automated tests** (HTML/JS). Verification is manual + `node --check` on `app.js`.

**Use the `frontend-design` skill** for the visual work: the grayscale inline-SVG icons (Apple Music / Spotify / SoundCloud / speaker collage + YouTube play glyph) and the mode-card / collage CSS must match the existing analog-gear aesthetic. The structure and JS logic below are deterministic; the icon SVG paths and card styling are produced during implementation with that skill.

- [ ] **Step 1: Restructure `index.html` into three views**

Replace the single `.tape` block inside `.hero__panel` with:
- `#mode-picker` — two `.mode-card` buttons: `data-mode="youtube"` (play glyph + "Record from YouTube") and `data-mode="system"` (icon collage + "Record from system audio").
- `#flow` (initially `hidden`) containing, in order:
  - a Back control: `<button id="back-btn" class="chip chip--ghost" type="button">‹ Back</button>`
  - `#yt-chrome` (the existing `.yt-load` input + `#yt-status` + `#yt-embed`)
  - `#sys-chrome` (the icon collage + explainer `<p>` with the text below)
  - the shared metadata block: `#song`, `#artist`, and a new `#image-url` field (`type="url"`, placeholder "Image URL for the tile (optional)"), where `#image-url` is wrapped so it can be shown only in system mode
  - the shared status block (`#rec-led`, `#tape-state`, `#tape-timer`) and `#capture-btn`, plus `#form-error`

System explainer text (in `#sys-chrome`): "Capture anything playing on your Mac — Apple Music, Spotify, SoundCloud, a file. You start and stop the capture yourself. Start the capture *before* the audio begins, then stop it when the song ends."

Keep all existing element ids (`#song`, `#artist`, `#yturl`, `#yt-load-btn`, `#yt-status`, `#yt-embed`, `#yt-iframe`, `#rec-led`, `#tape-state`, `#tape-timer`, `#capture-btn`, `#form-error`) so the existing `app.js` wiring keeps working.

- [ ] **Step 2: Add the mode state machine to `app.js`**

Add near the top (after the existing element lookups):

```javascript
// Landing mode: null (picker) | "youtube" | "system"
let mode = null;
const modePicker = document.getElementById("mode-picker");
const flow = document.getElementById("flow");
const ytChrome = document.getElementById("yt-chrome");
const sysChrome = document.getElementById("sys-chrome");
const imageUrlField = document.getElementById("image-url-field");
const backBtn = document.getElementById("back-btn");

function selectMode(m) {
  mode = m;
  modePicker.hidden = true;
  flow.hidden = false;
  ytChrome.hidden = m !== "youtube";
  sysChrome.hidden = m !== "system";
  imageUrlField.hidden = m !== "system";
  (m === "youtube" ? yturl : song).focus();
}

function goBack() {
  if (recording) return;           // Back is disabled mid-capture
  mode = null;
  flow.hidden = true;
  modePicker.hidden = false;
}

document.querySelectorAll(".mode-card").forEach((card) => {
  card.addEventListener("click", () => selectMode(card.dataset.mode));
});
backBtn.addEventListener("click", goBack);
```

- [ ] **Step 3: Make `begin()` mode-aware and disable Back while recording**

In `begin()`, replace the body construction so the request sends the right field per mode:

```javascript
  const body = { title, artist: artist.value.trim() };
  if (mode === "youtube") {
    body.youtube_url = yturl.value.trim();
  } else {
    const img = document.getElementById("image-url").value.trim();
    if (img) body.image_url = img;
  }
  const res = await fetch("/api/recordings/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
```

In `setState(rec)`, add so Back can't be used mid-capture:

```javascript
  backBtn.disabled = rec;
```

- [ ] **Step 4: Style the cards, collage, and back button (frontend-design skill)**

Locate the landing CSS (`ls songcoach/static/css/`), then add rules for `.mode-card`
(two side-by-side tiles that collapse to stacked on narrow widths), the `.icon-collage`
(the four grayscale glyphs), and `#back-btn` placement. Add the inline-SVG icons to the
cards/collage in `index.html`. Match the analog-gear look; keep everything self-contained.

- [ ] **Step 5: Verify (syntax + dev server, manual)**

```bash
node --check songcoach/static/js/app.js        # JS parses
.venv/bin/python -m uvicorn songcoach.main:app --port 8123 --log-level warning &
sleep 2
curl -s http://127.0.0.1:8123/ | grep -o 'id="mode-picker"\|data-mode="youtube"\|data-mode="system"\|id="image-url"'
```
Expected: the picker + both cards + the image-url field are present in the HTML.
Then open `http://localhost:8123/` in a browser and confirm: two cards render with icons;
YouTube card → existing flow + Back (Back disabled once recording); system card → song/
artist/image-url + explainer + capture; a valid image URL becomes the library tile
thumbnail after a capture. Stop the server when done.

- [ ] **Step 6: Commit**

```bash
git add songcoach/templates/index.html songcoach/static/js/app.js songcoach/static/css/
git commit -m "feat: landing mode picker — YouTube vs system audio

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** two-card picker (T3), YouTube flow + Back (T3), system flow with
  metadata/explainer/collage + image-url (T3), Back-disabled-while-recording (T3),
  image-URL fetch-and-store option A (T1) wired at start (T2), grayscale trademark-safe
  icons (T3). All spec sections covered.
- **Type consistency:** `image_url` flows `StartRecordingIn` (T2) → `store_image_from_url_async` (T1); the front-end `begin()` sends `image_url` only in system mode (T3).
- **YAGNI:** no persisted image URL, no edit/re-fetch of system thumbnails, no backend
  "mode" concept — all deferred per the spec's non-goals.
