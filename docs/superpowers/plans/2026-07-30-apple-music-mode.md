# Apple Music Auto-Capture Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Apple Music mode" that watches Music.app's transport and auto-captures each song (pause/resume with the transport, dispatch each finished song to a serial stem queue) until the user clicks Stop.

**Architecture:** A 1s `osascript` poll (`apple_music/watcher.py`) feeds `MusicState` samples to a state machine (`apple_music/session.py`) that drives per-song capture via a segment-and-concat recorder (`pipeline/segmented_recorder.py`) and enqueues finished songs to a single-worker FIFO (`stem_queue.py`). Cover artwork is best-effort-sourced from Music. A third landing-page mode card drives it via three API endpoints.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, `subprocess`/`osascript`/`ffmpeg` (macOS), pytest + `fastapi.testclient`, vanilla JS.

## Global Constraints

- **Run tests with** `.venv/bin/python -m pytest` (NOT `.venv/bin/pytest` — repo root isn't on `sys.path`).
- **Read the data root dynamically** as `Path(settings.local_storage_dir)` (via existing helpers like `capture_dir(job_id)` / `metadata.job_dir(job_id)`) so the `storage_dir` fixture's monkeypatch applies.
- **macOS-only externals** (`osascript`, `syscap`, `ffmpeg`) are always **mocked/monkeypatched** in tests; never shell out to them in a test.
- **`JobStatus`** values are `recording, queued, separating, uploading, done, failed`. A song being captured is `recording`; on dispatch it becomes `queued` with `progress=10` (mirroring `recording.stop()`).
- **Min song length** default is **5s** (`APPLE_MUSIC_MIN_SONG_SECONDS`); a finalized song shorter than that is **discarded** (Job row + its `recordings/<id>` and `jobs/<id>` dirs deleted), not queued.
- **Track identity** = Music's persistent ID string. `fast forwarding`/`rewinding` normalize to `playing`.
- **Reuse existing helpers**: `capture_dir(job_id)` (`pipeline/recorder.py`), `metadata.job_dir/thumbnail_path`, `RecordingResult`/`RecorderError` (`pipeline/recorder.py`), `metadata.write_meta`.
- Commit trailer on every commit:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Serial stem queue

**Files:**
- Create: `songcoach/stem_queue.py`
- Modify: `songcoach/jobs.py`
- Test: `tests/test_stem_queue.py`

**Interfaces:**
- Consumes: `songcoach.pipeline.process.process_capture` (lazily, inside the worker).
- Produces:
  - `stem_queue.enqueue(job_id: str) -> None` — puts the id on a FIFO drained by a single daemon worker; returns immediately.
  - `stem_queue._run_job(job_id: str) -> None` — the indirection the worker calls (patch point for tests).
  - `jobs.enqueue_processing(job_id)` now delegates to `stem_queue.enqueue`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stem_queue.py
import threading
import time

from songcoach import stem_queue


def test_enqueue_runs_serially_in_fifo_order(monkeypatch):
    order = []
    concurrent = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_run(job_id):
        with lock:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
        time.sleep(0.02)
        order.append(job_id)
        with lock:
            concurrent["now"] -= 1

    monkeypatch.setattr(stem_queue, "_run_job", fake_run)

    for jid in ["a", "b", "c", "d"]:
        stem_queue.enqueue(jid)
    stem_queue._queue.join()

    assert order == ["a", "b", "c", "d"]      # FIFO
    assert concurrent["max"] == 1             # never two at once


def test_worker_survives_a_failing_job(monkeypatch):
    seen = []

    def fake_run(job_id):
        seen.append(job_id)
        if job_id == "boom":
            raise RuntimeError("kaboom")

    monkeypatch.setattr(stem_queue, "_run_job", fake_run)
    for jid in ["boom", "after"]:
        stem_queue.enqueue(jid)
    stem_queue._queue.join()
    assert seen == ["boom", "after"]          # a failure doesn't kill the worker
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stem_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'songcoach.stem_queue'`.

- [ ] **Step 3: Write minimal implementation**

```python
# songcoach/stem_queue.py
"""A single-worker FIFO queue for the Demucs separation step.

Captures (manual or Apple Music mode) enqueue a job id and return immediately;
one daemon worker runs the slow separation one job at a time, so back-to-back
songs never spawn N concurrent Demucs runs. Not persisted — an interrupted job
is handled by the existing resume/rebuild path.
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger("songcoach.stem_queue")

_queue: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


def _run_job(job_id: str) -> None:
    # Imported lazily so importing this module doesn't pull in the pipeline.
    from .pipeline.process import process_capture
    process_capture(job_id)


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        except Exception:  # noqa: BLE001 — one bad job must not kill the worker
            log.exception("Stem worker failed for %s", job_id)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="stem-worker", daemon=True)
            _worker.start()


def enqueue(job_id: str) -> None:
    """Queue a captured job for separation. Returns immediately."""
    _ensure_worker()
    _queue.put(job_id)
    log.info("Enqueued %s for separation (queue depth ~%d)", job_id, _queue.qsize())
```

Then change `songcoach/jobs.py` to delegate. Replace its body with:

```python
"""Dispatch the separation pipeline for a job.

Single-user local app: captures enqueue onto a single-worker serial queue
(see stem_queue) so back-to-back songs stem one at a time.
"""
from __future__ import annotations

import logging

from . import stem_queue

log = logging.getLogger("songcoach.jobs")


def enqueue_processing(job_id: str) -> None:
    """Queue a captured recording for separation, off the request thread."""
    log.info("Enqueuing separation for %s", job_id)
    stem_queue.enqueue(job_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stem_queue.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full suite (no regressions in the manual path)**

Run: `.venv/bin/python -m pytest -q`
Expected: all prior tests still pass (the retry/recording paths now route through the queue).

- [ ] **Step 6: Commit**

```bash
git add songcoach/stem_queue.py songcoach/jobs.py tests/test_stem_queue.py
git commit -m "feat(stem-queue): serial single-worker queue for separation"
```

---

### Task 2: Segmented recorder (pause/resume via concat)

**Files:**
- Create: `songcoach/pipeline/segmented_recorder.py`
- Test: `tests/test_segmented_recorder.py`

**Interfaces:**
- Consumes: `pipeline.recorder.Recorder`, `RecordingResult`, `RecorderError`; `settings.ffmpeg_bin`.
- Produces: `SegmentedRecorder(out_dir: Path, *, max_seconds: int | None = None)` with `start()`, `pause()`, `resume()`, `finish() -> RecordingResult`. One song = 1+ segments concatenated into `out_dir/capture.m4a`; `finish()`'s `RecordingResult.duration` is the **sum of segment durations**.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_segmented_recorder.py
import subprocess
from pathlib import Path

import pytest

from songcoach.pipeline import segmented_recorder as sr
from songcoach.pipeline.recorder import RecordingResult


class FakeRecorder:
    """Stands in for the real syscap-backed Recorder: writes a stub segment file."""
    def __init__(self, out_dir, *, max_seconds=None):
        self.out_dir = Path(out_dir)
        self.audio_path = self.out_dir / "capture.m4a"

    def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.audio_path.write_bytes(b"seg-audio")

    def stop(self, timeout=10.0):
        return RecordingResult(audio_path=self.audio_path, duration=30.0)


@pytest.fixture
def fake_recorder(monkeypatch):
    monkeypatch.setattr(sr.recorder_mod, "Recorder", FakeRecorder)


def _fake_ffmpeg_ok(monkeypatch, dest_marker=b"concatenated"):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        # last arg is the dest path
        Path(cmd[-1]).write_bytes(dest_marker)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    return calls


def test_single_segment_skips_concat(fake_recorder, monkeypatch, tmp_path):
    calls = _fake_ffmpeg_ok(monkeypatch)
    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start()
    result = rec.finish()
    assert result.audio_path == tmp_path / "rec" / "capture.m4a"
    assert result.audio_path.exists()
    assert result.duration == 30.0
    assert calls == []                        # no ffmpeg for a single segment


def test_multi_segment_concatenates(fake_recorder, monkeypatch, tmp_path):
    calls = _fake_ffmpeg_ok(monkeypatch)
    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start()
    rec.pause()
    rec.resume()
    rec.pause()
    result = rec.finish()
    assert result.audio_path.exists()
    assert result.duration == 60.0            # two 30s segments summed
    assert len(calls) == 1                     # one concat invocation
    assert "-c" in calls[0] and "copy" in calls[0]


def test_concat_copy_failure_falls_back_to_reencode(fake_recorder, monkeypatch, tmp_path):
    attempts = []
    def fake_run(cmd, **kw):
        attempts.append(cmd)
        if "copy" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="copy failed")
        Path(cmd[-1]).write_bytes(b"reencoded")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(sr.subprocess, "run", fake_run)

    rec = sr.SegmentedRecorder(tmp_path / "rec")
    rec.start(); rec.pause(); rec.resume(); rec.pause()
    result = rec.finish()
    assert result.audio_path.read_bytes() == b"reencoded"
    assert len(attempts) == 2                  # copy attempt, then re-encode
    assert any("aac" in c for c in attempts[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_segmented_recorder.py -v`
Expected: FAIL — `No module named 'songcoach.pipeline.segmented_recorder'`.

- [ ] **Step 3: Write minimal implementation**

```python
# songcoach/pipeline/segmented_recorder.py
"""Record one song as 1+ segments (pause/resume) concatenated into one file.

Apple Music mode pauses/resumes capture with Music's transport. syscap only
does start/stop, so a "pause" finalizes the current segment and a "resume"
starts a new one; finish() concatenates all segments into capture.m4a (dead-air
from the pause is not recorded). Segments share identical AAC params (same
syscap), so ffmpeg stream-copy concat is valid, with a re-encode fallback.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..config import settings
from . import recorder as recorder_mod
from .recorder import RecorderError, RecordingResult

log = logging.getLogger("songcoach.segmented_recorder")


class SegmentedRecorder:
    def __init__(self, out_dir: Path, *, max_seconds: int | None = None):
        self.out_dir = Path(out_dir)
        self.max_seconds = max_seconds
        self.seg_dir = self.out_dir / "segments"
        self.capture_path = self.out_dir / "capture.m4a"
        self._segments: list[Path] = []
        self._durations: list[float] = []
        self._active: recorder_mod.Recorder | None = None

    def start(self) -> None:
        if self._segments or self._active is not None:
            raise RecorderError("segmented recorder already started")
        self._begin_segment()

    def _begin_segment(self) -> None:
        idx = len(self._segments)
        seg = self.seg_dir / f"{idx:03d}"
        rec = recorder_mod.Recorder(seg, max_seconds=self.max_seconds)
        rec.start()
        self._active = rec
        self._segments.append(seg / "capture.m4a")

    def _end_segment(self) -> None:
        if self._active is None:
            return
        result = self._active.stop()
        self._durations.append(result.duration or 0.0)
        self._active = None

    def pause(self) -> None:
        self._end_segment()

    def resume(self) -> None:
        if self._active is not None:
            raise RecorderError("resume while a segment is active")
        self._begin_segment()

    def finish(self) -> RecordingResult:
        self._end_segment()
        segs = [p for p in self._segments if p.exists() and p.stat().st_size > 0]
        if not segs:
            raise RecorderError("no audio captured")
        if len(segs) == 1:
            segs[0].replace(self.capture_path)
        else:
            self._concat(segs, self.capture_path)
        duration = sum(self._durations) or None
        log.info("Song finalized: %d segment(s), %.1fs → %s",
                 len(segs), duration or 0.0, self.capture_path)
        return RecordingResult(audio_path=self.capture_path, duration=duration)

    def _concat(self, segs: list[Path], dest: Path) -> None:
        listfile = self.seg_dir / "concat.txt"
        listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in segs), encoding="utf-8")
        base = [settings.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile)]
        try:
            subprocess.run(base + ["-c", "copy", str(dest)],
                           check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            log.warning("concat -c copy failed; re-encoding to AAC")
            subprocess.run(base + ["-c:a", "aac", "-b:a", "256k", str(dest)],
                           check=True, capture_output=True, text=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_segmented_recorder.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add songcoach/pipeline/segmented_recorder.py tests/test_segmented_recorder.py
git commit -m "feat(recorder): SegmentedRecorder — pause/resume via concat"
```

---

### Task 3: Cover artwork from Apple Music

**Files:**
- Modify: `songcoach/fetch_thumbnails.py`
- Create: `songcoach/apple_music/__init__.py` (empty), `songcoach/apple_music/artwork.py`
- Test: `tests/test_artwork_store.py`

**Interfaces:**
- Consumes: `metadata.thumbnail_path`, existing `_MAX_IMAGE_BYTES`.
- Produces:
  - `fetch_thumbnails.store_image_from_file(job_id: str, src_path: Path) -> bool` — copies a local image to the job's thumbnail (size-guarded); returns whether it stored.
  - `apple_music.artwork.fetch_artwork_async(job_id: str) -> None` — best-effort: `osascript`-export the current track's artwork, then `store_image_from_file`. Off-thread; swallows all errors.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artwork_store.py
from pathlib import Path

from songcoach import fetch_thumbnails, metadata


def test_store_image_from_file_writes_thumbnail(storage_dir, tmp_path):
    src = tmp_path / "art.jpg"
    src.write_bytes(b"\xff\xd8\xff" + b"x" * 500)   # small stub image
    ok = fetch_thumbnails.store_image_from_file("job1", src)
    assert ok is True
    dest = metadata.thumbnail_path("job1")
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()


def test_store_image_from_file_skips_missing(storage_dir, tmp_path):
    assert fetch_thumbnails.store_image_from_file("job2", tmp_path / "nope.jpg") is False
    assert not metadata.thumbnail_path("job2").exists()


def test_store_image_from_file_skips_oversized(storage_dir, tmp_path):
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * (fetch_thumbnails._MAX_IMAGE_BYTES + 1))
    assert fetch_thumbnails.store_image_from_file("job3", big) is False
    assert not metadata.thumbnail_path("job3").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_artwork_store.py -v`
Expected: FAIL — `AttributeError: module 'songcoach.fetch_thumbnails' has no attribute 'store_image_from_file'`.

- [ ] **Step 3: Write minimal implementation**

Add to `songcoach/fetch_thumbnails.py` (near `store_image_from_url`), importing `Path` if not already imported at the top (it is: `from pathlib import Path`):

```python
def store_image_from_file(job_id: str, src_path: "Path") -> bool:
    """Store a local image file as the job's thumbnail (best-effort, size-guarded).

    Returns True if it stored the image. Used for Apple Music cover art exported
    by osascript. Writes only the image file (not the sidecar), like
    store_image_from_url.
    """
    src = Path(src_path)
    try:
        if not src.is_file():
            return False
        size = src.stat().st_size
        if size == 0 or size > _MAX_IMAGE_BYTES:
            if size > _MAX_IMAGE_BYTES:
                log.warning("Artwork too large (%d bytes) for %s", size, job_id)
            return False
        from .metadata import thumbnail_path
        dest = thumbnail_path(job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        log.info("Stored artwork thumbnail for %s (%d KB)", job_id, size // 1024)
        return True
    except OSError:
        log.warning("Could not store artwork for %s", job_id, exc_info=True)
        return False
```

Create `songcoach/apple_music/__init__.py` (empty file).

Create `songcoach/apple_music/artwork.py`:

```python
"""Best-effort cover artwork from Apple Music for a captured song.

At song-begin we ask Music (via osascript) to write the current track's artwork
to a file, then store it as the job's thumbnail. Any failure (no artwork,
Automation denied, bad bytes) is swallowed — the tile just stays blank.
"""
from __future__ import annotations

import logging
import subprocess
import threading

from .. import fetch_thumbnails
from ..pipeline.recorder import capture_dir

log = logging.getLogger("songcoach.apple_music.artwork")

# Writes the current track's raw artwork bytes to outPath; returns "ok"/"none".
_ARTWORK_SCRIPT = '''
on run argv
  set outPath to item 1 of argv
  tell application "Music"
    if not (exists current track) then return "none"
    if (count of artworks of current track) is 0 then return "none"
    set d to raw data of artwork 1 of current track
  end tell
  set fh to open for access (POSIX file outPath) with write permission
  set eof fh to 0
  write d to fh
  close access fh
  return "ok"
end run
'''


def _export_artwork(out_file) -> bool:
    try:
        res = subprocess.run(
            ["osascript", "-e", _ARTWORK_SCRIPT, str(out_file)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and res.stdout.strip() == "ok"


def fetch_and_store(job_id: str) -> None:
    out_file = capture_dir(job_id) / "artwork.dat"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if _export_artwork(out_file):
        fetch_thumbnails.store_image_from_file(job_id, out_file)
    out_file.unlink(missing_ok=True)


def fetch_artwork_async(job_id: str) -> None:
    threading.Thread(target=fetch_and_store, args=(job_id,), daemon=True).start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_artwork_store.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add songcoach/fetch_thumbnails.py songcoach/apple_music/__init__.py songcoach/apple_music/artwork.py tests/test_artwork_store.py
git commit -m "feat(apple-music): best-effort cover artwork → thumbnail"
```

---

### Task 4: Music watcher (poll + parse)

**Files:**
- Create: `songcoach/apple_music/watcher.py`
- Test: `tests/test_apple_music_watcher.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) MusicState(state: str, track_id: str|None = None, name: str|None = None, artist: str|None = None)` — `state ∈ {"playing","paused","stopped","closed"}`.
  - `parse_music_line(raw: str) -> MusicState` (pure).
  - `MusicWatcher(on_state: Callable[[MusicState], None], *, interval: float = 1.0)` with `start()`, `stop()`, and a `permission_error: bool` attribute; polls Music via `osascript` and calls `on_state` each tick.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apple_music_watcher.py
from songcoach.apple_music.watcher import MusicState, parse_music_line


def test_parse_not_running():
    assert parse_music_line("not running") == MusicState("closed")


def test_parse_stopped():
    assert parse_music_line("stopped") == MusicState("stopped")


def test_parse_playing_with_track():
    s = parse_music_line("playing\tPID123\tSong Name\tThe Artist")
    assert s == MusicState("playing", "PID123", "Song Name", "The Artist")


def test_parse_paused():
    s = parse_music_line("paused\tPID9\tB\tArt")
    assert s.state == "paused" and s.track_id == "PID9"


def test_fast_forwarding_normalizes_to_playing():
    s = parse_music_line("fast forwarding\tPID1\tX\tY")
    assert s.state == "playing" and s.track_id == "PID1"


def test_rewinding_normalizes_to_playing():
    assert parse_music_line("rewinding\tPID1\tX\tY").state == "playing"


def test_unparseable_is_closed():
    assert parse_music_line("garble").state == "closed"
    assert parse_music_line("").state == "closed"
    assert parse_music_line("playing\tonlytwo").state == "closed"


def test_empty_track_fields_become_none():
    s = parse_music_line("playing\tPID\t\t")
    assert s.track_id == "PID" and s.name is None and s.artist is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_apple_music_watcher.py -v`
Expected: FAIL — `No module named 'songcoach.apple_music.watcher'`.

- [ ] **Step 3: Write minimal implementation**

```python
# songcoach/apple_music/watcher.py
"""Poll Apple Music's transport once per second via osascript.

Emits a MusicState per tick to a callback. The AppleScript is guarded so it
never launches Music. The first call triggers the one-time Automation TCC
prompt; a denial surfaces as permission_error.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("songcoach.apple_music.watcher")

_SCRIPT = '''
if application "Music" is running then
  tell application "Music"
    set st to (player state as text)
    if st is "stopped" then
      return "stopped"
    else
      set t to current track
      return st & tab & (persistent ID of t) & tab & (name of t) & tab & (artist of t)
    end if
  end tell
else
  return "not running"
end if
'''


@dataclass(frozen=True)
class MusicState:
    state: str                    # "playing" | "paused" | "stopped" | "closed"
    track_id: str | None = None
    name: str | None = None
    artist: str | None = None


def parse_music_line(raw: str) -> MusicState:
    raw = (raw or "").strip()
    if raw == "not running":
        return MusicState("closed")
    if raw == "stopped":
        return MusicState("stopped")
    parts = raw.split("\t")
    if len(parts) >= 4:
        st, tid, name, artist = parts[0], parts[1], parts[2], parts[3]
        if st in ("fast forwarding", "rewinding"):
            st = "playing"
        if st in ("playing", "paused"):
            return MusicState(st, tid or None, name or None, artist or None)
    return MusicState("closed")   # fail safe → ARMED, never a false capture


class MusicWatcher:
    def __init__(self, on_state: Callable[[MusicState], None], *, interval: float = 1.0):
        self._on_state = on_state
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.permission_error = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="music-watcher", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            state = self._poll()
            if state is not None:
                try:
                    self._on_state(state)
                except Exception:  # noqa: BLE001 — a handler error must not kill the loop
                    log.exception("Apple Music state handler failed")
            if self._stop.wait(self._interval):
                break

    def _poll(self) -> MusicState | None:
        try:
            res = subprocess.run(["osascript", "-e", _SCRIPT],
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode != 0:
            err = (res.stderr or "")
            self.permission_error = ("-1743" in err) or ("Not author" in err)
            return None
        self.permission_error = False
        return parse_music_line(res.stdout)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_apple_music_watcher.py -v`
Expected: PASS (all parse tests).

- [ ] **Step 5: Commit**

```bash
git add songcoach/apple_music/watcher.py tests/test_apple_music_watcher.py
git commit -m "feat(apple-music): osascript watcher + state parser"
```

---

### Task 5: Session state machine (+ recording guard)

**Files:**
- Create: `songcoach/apple_music/session.py`
- Modify: `songcoach/recording.py` (mutual-exclusion guard)
- Test: `tests/test_apple_music_session.py`, `tests/test_recording_guard.py`

**Interfaces:**
- Consumes: `MusicState` (Task 4), `SegmentedRecorder` (Task 2), `stem_queue.enqueue` (Task 1), `apple_music.artwork.fetch_artwork_async` (Task 3), `capture_dir`, `metadata.job_dir`, `SessionLocal`, `Job`, `JobStatus`, `settings.max_duration_seconds`.
- Produces:
  - `recording.set_apple_music_active(bool)`, `recording.apple_music_active() -> bool`; `recording.start()` now raises `RecorderError` if AM mode is active.
  - `AppleMusicSession(*, min_song_seconds: int = 5)` with `start()`, `on_state(s: MusicState)`, `stop()`, `status() -> dict`.

- [ ] **Step 1 (guard): Write the failing test**

```python
# tests/test_recording_guard.py
import pytest

from songcoach import recording
from songcoach.pipeline.recorder import RecorderError


def test_manual_start_blocked_while_apple_music_active(storage_dir, monkeypatch):
    recording.set_apple_music_active(True)
    try:
        with pytest.raises(RecorderError):
            recording.start(title="X")
    finally:
        recording.set_apple_music_active(False)
    assert recording.apple_music_active() is False
```

- [ ] **Step 2 (guard): Run it, verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recording_guard.py -v`
Expected: FAIL — `AttributeError: module 'songcoach.recording' has no attribute 'set_apple_music_active'`.

- [ ] **Step 3 (guard): Implement in `songcoach/recording.py`**

Add near the module-level state (after `_active`):

```python
_apple_music_active = False


def apple_music_active() -> bool:
    return _apple_music_active


def set_apple_music_active(value: bool) -> None:
    global _apple_music_active
    _apple_music_active = bool(value)
```

And at the top of `start()`, inside the `with _lock:` block, before the `_active` check, add:

```python
        if _apple_music_active:
            raise RecorderError("Apple Music mode is running — stop it first")
```

- [ ] **Step 4 (guard): Run it, verify it passes**

Run: `.venv/bin/python -m pytest tests/test_recording_guard.py -v`
Expected: PASS.

- [ ] **Step 5 (session): Write the failing test**

```python
# tests/test_apple_music_session.py
import pytest

from songcoach import stem_queue
from songcoach.apple_music import session as session_mod
from songcoach.apple_music.session import AppleMusicSession
from songcoach.apple_music.watcher import MusicState
from songcoach.db import SessionLocal
from songcoach.models import Job, JobStatus
from songcoach.pipeline.recorder import RecordingResult


class FakeSegRec:
    """Records the segment lifecycle; finish() returns a configurable duration."""
    duration = 30.0
    instances = []

    def __init__(self, out_dir, *, max_seconds=None):
        self.out_dir = out_dir
        self.calls = []
        FakeSegRec.instances.append(self)

    def start(self): self.calls.append("start")
    def pause(self): self.calls.append("pause")
    def resume(self): self.calls.append("resume")
    def finish(self):
        self.calls.append("finish")
        return RecordingResult(audio_path=self.out_dir / "capture.m4a",
                               duration=FakeSegRec.duration)


@pytest.fixture
def wired(monkeypatch, storage_dir):
    FakeSegRec.instances = []
    FakeSegRec.duration = 30.0
    enqueued = []
    monkeypatch.setattr(session_mod, "SegmentedRecorder", FakeSegRec)
    monkeypatch.setattr(session_mod.stem_queue, "enqueue", lambda jid: enqueued.append(jid))
    monkeypatch.setattr(session_mod.artwork, "fetch_artwork_async", lambda jid: None)
    return enqueued


def _play(tid, name="Song", artist="Artist"):
    return MusicState("playing", tid, name, artist)


def test_single_song_play_then_stop_dispatches(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    assert s.status()["phase"] == "capturing"
    s.on_state(MusicState("stopped"))
    assert wired == [_only_job_id(s)] or len(wired) == 1
    assert FakeSegRec.instances[0].calls == ["start", "finish"]
    s.stop()


def test_pause_resume_is_one_job_two_segments(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    s.on_state(MusicState("paused", "A", "Song", "Artist"))
    assert s.status()["phase"] == "paused"
    s.on_state(_play("A"))                       # same track resumes
    s.on_state(MusicState("stopped"))
    assert len(wired) == 1                         # one job dispatched
    assert FakeSegRec.instances[0].calls == ["start", "pause", "resume", "finish"]
    assert len(FakeSegRec.instances) == 1         # only one recorder → one song
    s.stop()


def test_track_change_finalizes_and_starts_next(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    s.on_state(_play("B"))                         # continuous advance
    assert len(wired) == 1                         # A dispatched
    assert len(FakeSegRec.instances) == 2         # A finished, B started
    s.on_state(MusicState("stopped"))
    assert len(wired) == 2
    s.stop()


def test_short_song_is_discarded(wired, storage_dir):
    FakeSegRec.duration = 2.0                      # below 5s
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    job_id = _only_job_id(s)
    s.on_state(MusicState("stopped"))
    assert wired == []                             # not enqueued
    assert SessionLocal().get(Job, job_id) is None  # job row deleted
    s.stop()


def test_stop_button_finalizes_current(wired):
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    s.stop()
    assert len(wired) == 1                         # current song dispatched on Stop
    assert s.status()["active"] is False


def test_mid_song_entry_captures_current(wired):
    # Mode starts while a song already plays → begins capturing immediately.
    s = AppleMusicSession()
    s.start()
    s.on_state(_play("A"))
    assert s.status()["phase"] == "capturing"
    s.stop()


def _only_job_id(session):
    # The session exposes its current job id via status()/internal for the test.
    return session._job_id
```

- [ ] **Step 6 (session): Run it, verify it fails**

Run: `.venv/bin/python -m pytest tests/test_apple_music_session.py -v`
Expected: FAIL — `No module named 'songcoach.apple_music.session'`.

- [ ] **Step 7 (session): Implement `songcoach/apple_music/session.py`**

```python
"""Apple Music mode: drive per-song capture from Music transport events.

Consumes MusicState samples (one per watcher poll) and diffs against the
previous phase to begin/pause/resume/finalize song captures, dispatching each
finished song (>= min length) to the serial stem queue. A song is one Job made
of 1+ SegmentedRecorder segments.
"""
from __future__ import annotations

import logging
import shutil

from .. import metadata, recording, stem_queue
from ..config import settings
from ..db import SessionLocal
from ..models import Job, JobStatus
from ..pipeline.recorder import RecorderError, capture_dir
from ..pipeline.segmented_recorder import SegmentedRecorder
from ..apple_music import artwork
from .watcher import MusicState

log = logging.getLogger("songcoach.apple_music.session")


class AppleMusicSession:
    def __init__(self, *, min_song_seconds: int = 5):
        self._min = min_song_seconds
        self._active = False
        self._phase = "armed"                 # armed | capturing | paused
        self._job_id: str | None = None
        self._track_id: str | None = None
        self._recorder: SegmentedRecorder | None = None
        self._current: dict | None = None     # {"name","artist"}
        self._captured: list[dict] = []        # dispatched songs, newest first

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        recording.set_apple_music_active(True)
        self._active = True
        self._phase = "armed"
        log.info("Apple Music mode started")

    def stop(self) -> None:
        if self._recorder is not None:
            self._finalize_current()
        self._active = False
        self._phase = "armed"
        recording.set_apple_music_active(False)
        log.info("Apple Music mode stopped")

    def status(self) -> dict:
        return {
            "active": self._active,
            "phase": self._phase,
            "current": dict(self._current) if self._phase in ("capturing", "paused") else None,
            "captured": list(self._captured),
        }

    # ---- event handling --------------------------------------------------
    def on_state(self, s: MusicState) -> None:
        if not self._active:
            return
        if s.state == "playing":
            if self._phase == "armed":
                self._begin_song(s)
            elif self._phase == "paused":
                if s.track_id == self._track_id:
                    self._resume_song()
                else:
                    self._finalize_current()
                    self._begin_song(s)
            elif self._phase == "capturing":
                if s.track_id != self._track_id:
                    self._finalize_current()
                    self._begin_song(s)
        elif s.state == "paused":
            if self._phase == "capturing":
                self._pause_song()
        elif s.state in ("stopped", "closed"):
            if self._phase in ("capturing", "paused"):
                self._finalize_current()
                self._phase = "armed"

    # ---- actions ---------------------------------------------------------
    def _begin_song(self, s: MusicState) -> None:
        job_id = self._create_job(s.name or "Untitled", s.artist)
        recorder = SegmentedRecorder(capture_dir(job_id),
                                     max_seconds=settings.max_duration_seconds)
        try:
            recorder.start()
        except RecorderError as exc:
            log.error("Could not start capture for %s: %s", job_id, exc)
            self._mark_failed(job_id, str(exc))
            self._phase = "armed"
            self._job_id = self._recorder = self._current = self._track_id = None
            return
        self._job_id = job_id
        self._track_id = s.track_id
        self._recorder = recorder
        self._current = {"name": s.name, "artist": s.artist}
        self._phase = "capturing"
        artwork.fetch_artwork_async(job_id)
        log.info("Capturing '%s' — %s (%s)", s.name, s.artist, job_id)

    def _pause_song(self) -> None:
        self._recorder.pause()
        self._phase = "paused"

    def _resume_song(self) -> None:
        self._recorder.resume()
        self._phase = "capturing"

    def _finalize_current(self) -> None:
        recorder, job_id, current = self._recorder, self._job_id, self._current
        self._recorder = self._job_id = self._current = self._track_id = None
        if recorder is None or job_id is None:
            return
        try:
            result = recorder.finish()
        except RecorderError as exc:
            log.warning("Finalize failed for %s: %s", job_id, exc)
            self._discard_job(job_id)
            return
        duration = result.duration or 0.0
        if duration < self._min:
            log.info("Discarding short song %s (%.1fs < %ds)", job_id, duration, self._min)
            self._discard_job(job_id)
            return
        self._stamp_and_enqueue(job_id, duration)
        self._captured.insert(0, {"job_id": job_id,
                                  "title": (current or {}).get("name"),
                                  "artist": (current or {}).get("artist")})

    # ---- persistence helpers --------------------------------------------
    def _create_job(self, title: str, artist: str | None) -> str:
        session = SessionLocal()
        try:
            job = Job(title=title, artist=artist, status=JobStatus.recording, progress=0)
            session.add(job)
            session.commit()
            return job.id
        finally:
            session.close()

    def _stamp_and_enqueue(self, job_id: str, duration: float) -> None:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.duration_seconds = duration
            job.status = JobStatus.queued
            job.progress = 10
            session.commit()
        finally:
            session.close()
        stem_queue.enqueue(job_id)

    def _mark_failed(self, job_id: str, message: str) -> None:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.failed
                job.error = message
                session.commit()
        finally:
            session.close()

    def _discard_job(self, job_id: str) -> None:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is not None:
                session.delete(job)
                session.commit()
        finally:
            session.close()
        shutil.rmtree(capture_dir(job_id), ignore_errors=True)
        shutil.rmtree(metadata.job_dir(job_id), ignore_errors=True)
```

- [ ] **Step 8 (session): Run it, verify it passes**

Run: `.venv/bin/python -m pytest tests/test_apple_music_session.py tests/test_recording_guard.py -v`
Expected: PASS (all session + guard tests).

- [ ] **Step 9: Commit**

```bash
git add songcoach/apple_music/session.py songcoach/recording.py tests/test_apple_music_session.py tests/test_recording_guard.py
git commit -m "feat(apple-music): session state machine + mutual-exclusion guard"
```

---

### Task 6: API endpoints + service singleton

**Files:**
- Create: `songcoach/apple_music/service.py`
- Modify: `songcoach/routes/api.py`
- Test: `tests/test_apple_music_api.py`

**Interfaces:**
- Consumes: `AppleMusicSession`, `MusicWatcher`, `recording.is_recording`.
- Produces:
  - `service.start_mode() -> dict`, `service.stop_mode() -> dict`, `service.status() -> dict`; raises `service.ModeError` when busy.
  - `POST /api/apple-music/start` (409 if manual recording in progress or mode already active), `POST /api/apple-music/stop`, `GET /api/apple-music/status`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apple_music_api.py
import pytest
from fastapi.testclient import TestClient


class FakeWatcher:
    def __init__(self, on_state, *, interval=1.0):
        self.on_state = on_state
        self.permission_error = False
        self.started = False
    def start(self): self.started = True
    def stop(self): self.started = False


@pytest.fixture
def client(storage_dir, monkeypatch):
    from songcoach.apple_music import service
    # Don't spawn real osascript threads.
    monkeypatch.setattr(service, "MusicWatcher", FakeWatcher)
    # Reset any leftover global mode between tests.
    service._reset_for_tests()
    from songcoach.main import app
    return TestClient(app)


def test_start_status_stop_cycle(client):
    r = client.post("/api/apple-music/start")
    assert r.status_code == 200
    assert r.json()["active"] is True

    s = client.get("/api/apple-music/status").json()
    assert s["active"] is True and s["phase"] == "armed"
    assert s["captured"] == [] and "permission_error" in s

    r = client.post("/api/apple-music/stop")
    assert r.status_code == 200
    assert client.get("/api/apple-music/status").json()["active"] is False


def test_start_409_when_already_active(client):
    client.post("/api/apple-music/start")
    assert client.post("/api/apple-music/start").status_code == 409
    client.post("/api/apple-music/stop")


def test_start_409_when_manual_recording(client, monkeypatch):
    from songcoach import recording
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    assert client.post("/api/apple-music/start").status_code == 409
```

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv/bin/python -m pytest tests/test_apple_music_api.py -v`
Expected: FAIL — routes 404 / `No module named 'songcoach.apple_music.service'`.

- [ ] **Step 3: Implement `songcoach/apple_music/service.py`**

```python
"""Process-wide holder for the single Apple Music mode session + watcher."""
from __future__ import annotations

import threading

from .. import recording
from .session import AppleMusicSession
from .watcher import MusicWatcher

_lock = threading.Lock()
_session: AppleMusicSession | None = None
_watcher: MusicWatcher | None = None


class ModeError(RuntimeError):
    """Raised when the mode can't start (busy) — mapped to 409 at the route."""


def start_mode() -> dict:
    global _session, _watcher
    with _lock:
        if _session is not None and _session.status()["active"]:
            raise ModeError("Apple Music mode is already running")
        if recording.is_recording():
            raise ModeError("Stop the current recording first")
        session = AppleMusicSession()
        watcher = MusicWatcher(on_state=session.on_state)
        session.start()
        watcher.start()
        _session, _watcher = session, watcher
        return _status_locked()


def stop_mode() -> dict:
    global _session, _watcher
    with _lock:
        if _watcher is not None:
            _watcher.stop()
        if _session is not None:
            _session.stop()
        status = _status_locked()
        _session = _watcher = None
        return status


def status() -> dict:
    with _lock:
        return _status_locked()


def _status_locked() -> dict:
    if _session is None:
        return {"active": False, "phase": "armed", "current": None,
                "captured": [], "permission_error": False}
    data = _session.status()
    data["permission_error"] = bool(_watcher and _watcher.permission_error)
    return data


def _reset_for_tests() -> None:
    global _session, _watcher
    with _lock:
        if _watcher is not None:
            _watcher.stop()
        _session = _watcher = None
    recording.set_apple_music_active(False)
```

- [ ] **Step 4: Add the endpoints to `songcoach/routes/api.py`**

Add the import near the other `from ..` imports:

```python
from ..apple_music import service as apple_music_service
```

Add these endpoints at the end of the file:

```python
@router.post("/apple-music/start")
def apple_music_start():
    try:
        return apple_music_service.start_mode()
    except apple_music_service.ModeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/apple-music/stop")
def apple_music_stop():
    return apple_music_service.stop_mode()


@router.get("/apple-music/status")
def apple_music_status():
    return apple_music_service.status()
```

- [ ] **Step 5: Run it, verify it passes + full suite**

Run: `.venv/bin/python -m pytest tests/test_apple_music_api.py -v`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: whole suite green.

- [ ] **Step 6: Commit**

```bash
git add songcoach/apple_music/service.py songcoach/routes/api.py tests/test_apple_music_api.py
git commit -m "feat(api): Apple Music mode start/stop/status endpoints"
```

---

### Task 7: Frontend (third mode card + panel) + README

**Files:**
- Modify: `songcoach/templates/index.html` (third card, `#am-chrome` panel, load script)
- Create: `songcoach/static/js/apple-music.js`
- Modify: `songcoach/static/js/app.js` (`selectMode` handles the third mode)
- Modify: `songcoach/static/css/styles.css` (panel styles + hide manual controls in AM mode)
- Modify: `README.md` (roadmap tick + short note)

**Interfaces:**
- Consumes: `POST /api/apple-music/start|stop`, `GET /api/apple-music/status`.

- [ ] **Step 1: Add the third mode card**

In `songcoach/templates/index.html`, inside `.mode-grid` (after the `data-mode="system"` card's closing `</button>`, before `</div>` that closes `.mode-grid`), add:

```html
        <button type="button" class="mode-card" data-mode="applemusic">
          <span class="mode-card__screen">
            <svg viewBox="0 0 24 24" class="glyph" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M14 7.5l-4 1v6.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="8.4" cy="15" r="2" fill="currentColor"/><path d="M14 8.5V12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          </span>
          <span class="mode-card__title">Apple Music</span>
          <span class="mode-card__sub">Auto-capture song after song</span>
        </button>
```

- [ ] **Step 2: Add the `#am-chrome` panel**

In `songcoach/templates/index.html`, inside `#flow`, after the `#sys-chrome` block's closing `</div>` (line ~74) and before `<!-- Shared details -->`, add:

```html
      <!-- Apple Music chrome -->
      <div id="am-chrome" hidden>
        <span class="tape__label">Apple Music mode</span>
        <p class="tape__hint">Play a song or playlist in Apple Music — SongCoach captures each song, pausing with the music, and queues every finished song for stems. It keeps going until you hit Stop.</p>
        <p id="am-perm" class="jack__error" role="alert" hidden>
          SongCoach needs permission to read Apple Music. Grant it in System Settings → Privacy &amp; Security → Automation, then try again.
        </p>
        <div class="am-status">
          <span id="am-led" class="rec-led" data-on="false"></span>
          <span id="am-state" class="tape__state">Not started</span>
        </div>
        <ul id="am-captured" class="am-captured"></ul>
        <button id="am-start" type="button" class="btn-rec" data-recording="false">
          <span class="btn-rec__icon"></span>
          <span class="btn-rec__label">START APPLE MUSIC MODE</span>
        </button>
        <button id="am-stop" type="button" class="chip chip--ghost" hidden>Stop</button>
      </div>
```

- [ ] **Step 3: Load the script**

In `songcoach/templates/index.html`, change the scripts block from:

```html
{% block scripts %}<script src="/static/js/app.js"></script><script src="/static/js/library.js"></script>{% endblock %}
```

to:

```html
{% block scripts %}<script src="/static/js/app.js"></script><script src="/static/js/library.js"></script><script src="/static/js/apple-music.js"></script>{% endblock %}
```

- [ ] **Step 4: Teach `selectMode` about the third mode**

In `songcoach/static/js/app.js`, add a reference near the other chrome refs (after line ~23 `const sysChrome = ...`):

```javascript
const amChrome = document.getElementById("am-chrome");
```

Replace the body of `selectMode` (lines ~28-36) with:

```javascript
function selectMode(m) {
  mode = m;
  modePicker.hidden = true;
  flow.hidden = false;
  ytChrome.hidden = m !== "youtube";
  sysChrome.hidden = m !== "system";
  amChrome.hidden = m !== "applemusic";
  imageUrlField.hidden = m !== "system";
  // Apple Music mode has its own controls; hide the manual capture UI.
  flow.classList.toggle("mode-am", m === "applemusic");
  if (m === "youtube") yturl.focus();
  else if (m === "system") song.focus();
}
```

- [ ] **Step 5: Add `apple-music.js`**

Create `songcoach/static/js/apple-music.js`:

```javascript
// Apple Music mode: drive the auto-capture session and poll its status.
const amStart = document.getElementById("am-start");
const amStop = document.getElementById("am-stop");
const amState = document.getElementById("am-state");
const amLed = document.getElementById("am-led");
const amCaptured = document.getElementById("am-captured");
const amPerm = document.getElementById("am-perm");
const amBack = document.getElementById("back-btn");

let amPollId = null;

const PHASE_LABEL = {
  armed: "Waiting for Apple Music…",
  capturing: "● Capturing",
  paused: "❚❚ Paused",
};

function amRender(s) {
  const active = !!s.active;
  amStart.hidden = active;
  amStop.hidden = !active;
  amLed.dataset.on = active && s.phase !== "armed" ? "true" : "false";
  if (amBack) amBack.disabled = active;           // no leaving mid-session
  amPerm.hidden = !s.permission_error;

  let label = active ? PHASE_LABEL[s.phase] || "Active" : "Not started";
  if (active && s.current && (s.phase === "capturing" || s.phase === "paused")) {
    const who = s.current.artist ? ` — ${s.current.artist}` : "";
    label += `: ${s.current.name || "Unknown"}${who}`;
  }
  amState.textContent = label;

  amCaptured.innerHTML = "";
  (s.captured || []).forEach((c) => {
    const li = document.createElement("li");
    li.textContent = c.artist ? `${c.title} · ${c.artist}` : c.title || "Untitled";
    amCaptured.appendChild(li);
  });
}

async function amPoll() {
  try {
    const s = await (await fetch("/api/apple-music/status")).json();
    amRender(s);
    if (!s.active && amPollId) { clearInterval(amPollId); amPollId = null; }
  } catch {}
}

function amStartPolling() {
  if (!amPollId) amPollId = setInterval(amPoll, 1500);
}

amStart?.addEventListener("click", async () => {
  amStart.disabled = true;
  try {
    const res = await fetch("/api/apple-music/start", { method: "POST" });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      amState.textContent = d.detail || "Could not start.";
      return;
    }
    amRender(await res.json());
    amStartPolling();
  } finally {
    amStart.disabled = false;
  }
});

amStop?.addEventListener("click", async () => {
  amStop.disabled = true;
  try {
    const res = await fetch("/api/apple-music/stop", { method: "POST" });
    amRender(await res.json());
    if (amPollId) { clearInterval(amPollId); amPollId = null; }
  } finally {
    amStop.disabled = false;
  }
});

// If the mode is already running (page reload), restore the panel.
(async () => {
  try {
    const s = await (await fetch("/api/apple-music/status")).json();
    if (s.active) {
      if (typeof selectMode === "function") selectMode("applemusic");
      amRender(s);
      amStartPolling();
    }
  } catch {}
})();
```

- [ ] **Step 6: Add styles**

Append to `songcoach/static/css/styles.css`:

```css
/* Apple Music mode */
#flow.mode-am .meta,
#flow.mode-am .tape__status,
#flow.mode-am #capture-btn,
#flow.mode-am #form-error { display: none; }

#am-chrome { display: none; }
#flow.mode-am #am-chrome { display: block; }

.am-status { display: flex; align-items: center; gap: 0.6rem; margin: 0.8rem 0; }
.am-captured { list-style: none; margin: 0.6rem 0 1rem; padding: 0; max-height: 180px; overflow-y: auto; }
.am-captured li { padding: 0.35rem 0; border-bottom: 1px solid rgba(0,0,0,0.08); font-size: 0.95rem; }
.am-captured li:last-child { border-bottom: none; }
#am-stop { margin-top: 0.6rem; }
```

- [ ] **Step 7: Verify in the browser (controller runs this — see dispatch note)**

The controller will run the browser acceptance against an isolated data dir with `service.MusicWatcher` behavior exercised via the real endpoints (Apple Music itself need not be running — the panel shows the "armed" state). Implementer verification instead:

1. `.venv/bin/python -m pytest -q` → whole suite green.
2. Render smoke-check:
```
.venv/bin/python -m uvicorn songcoach.main:app --port 8140 >/tmp/am.log 2>&1 &
SRV=$!; sleep 4
curl -s http://127.0.0.1:8140/ | grep -o -E 'data-mode="applemusic"|id="am-(chrome|start|stop|state|captured)"' | sort -u
curl -s http://127.0.0.1:8140/api/apple-music/status
kill $SRV
```
Expect the card + all `am-*` ids present and `status` returns `{"active": false, ...}`.

- [ ] **Step 8: Update README**

In `README.md`, under **Using it**, after the "Record from system audio" bullet (before "Either way, Demucs runs…"), add:

```markdown
**♫ Apple Music mode** — hit **Start Apple Music mode**, then play a song or
playlist in Apple Music. SongCoach captures each song automatically, pauses when
you pause, and queues every finished song for stems — song after song — until you
click **Stop**. (First use asks macOS for permission to read Apple Music.)
```

In the **Roadmap** section, add above the "Delete/cleanup recordings" line:

```markdown
- ✅ Apple Music mode — auto-capture a playlist song-by-song into the stem queue
```

- [ ] **Step 9: Commit**

```bash
git add songcoach/templates/index.html songcoach/static/js/apple-music.js songcoach/static/js/app.js songcoach/static/css/styles.css README.md
git commit -m "feat(ui): Apple Music mode card + session panel"
```

---

## Self-Review

**Spec coverage:**
- Detection (osascript poll, playing/paused/stopped, ff/rw→playing, never launches Music, permission_error) → Task 4 (`watcher.py`) + Task 6 (`service` surfaces `permission_error`). ✓
- State machine (armed/capturing/paused, track-change, stopped→dispatch, Stop button, mid-song entry, ≥5s filter) → Task 5. ✓
- Pause/resume via segments + concat (copy → re-encode fallback, single-seg shortcut, summed duration) → Task 2. ✓
- Serial stem queue (single worker, FIFO, `jobs.enqueue_processing` delegates) → Task 1. ✓
- Cover artwork (best-effort osascript export → `store_image_from_file`) → Task 3. ✓
- Mutual exclusion (manual start 409 while AM active; AM start 409 while recording) → Task 5 guard + Task 6 endpoint. ✓
- API start/stop/status → Task 6. ✓
- Third-card UI + panel + README → Task 7. ✓
- Tests enumerated in the spec (watcher parse, state machine sequences, segmented recorder, serial queue, API, artwork store) → all present across Tasks 1–7. ✓

**Placeholder scan:** No TBD/TODO; every code step carries concrete code. ✓

**Type consistency:** `MusicState(state, track_id, name, artist)` used identically in Tasks 4/5/6. `SegmentedRecorder.start/pause/resume/finish()` and `RecordingResult(audio_path, duration)` consistent in Tasks 2/5. `stem_queue.enqueue(job_id)` consistent in Tasks 1/5. `service.start_mode/stop_mode/status/ModeError` consistent in Task 6 + its test. `status()` dict shape (`active, phase, current, captured, permission_error`) consistent between Task 6 and Task 7's `amRender`. ✓

**Notes for implementers:**
- `routes/api.py` already imports `HTTPException` — Task 6 reuses it (no new import beyond `apple_music_service`).
- Task 5's session references `SegmentedRecorder`, `stem_queue`, and `artwork` as module attributes so the tests can monkeypatch them (`session_mod.SegmentedRecorder`, `session_mod.stem_queue.enqueue`, `session_mod.artwork.fetch_artwork_async`).
- The `_only_job_id`/`session._job_id` access in Task 5's tests reads a private attribute deliberately (white-box) — acceptable for a state-machine unit test.
