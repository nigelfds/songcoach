# Apple Music auto-capture mode — design

**Date:** 2026-07-30
**Status:** Approved, ready for plan

## Goal

Add an "Apple Music mode" to SongCoach. While active, the app watches the local
Apple Music (Music.app) transport and **automatically captures each song** from
the system-audio tap: it starts a capture when a song plays, pauses/resumes the
capture with Music's transport, and finalizes + dispatches each finished song to
a stemming queue — song after song through a playlist — until the user clicks the
app's **Stop** button.

This reuses the existing capture path (`native/syscap` → `.m4a` → Demucs). The
new work is: detecting Music's playback events, a state-machine controller that
drives per-song capture, pause/resume via audio segments, and a real serial
stem queue so back-to-back songs don't spawn N concurrent Demucs runs.

## Non-goals

- Not Spotify / SoundCloud / other players (Apple Music only; the same
  system-audio tap works for any of them via the existing manual "system audio"
  mode, but auto-detection here is Music-specific).
- No editing/trimming of auto-captured songs beyond the concat of pause segments.
- No change to the player, the stem format, or the data model / `meta.json`
  schema. Auto-captured songs are ordinary jobs.
- No **album** field (would need a Job-model + schema + UI change for little
  payoff). Title, artist, and **cover artwork** are sourced; album is not.
- The job's `duration_seconds` stays the **measured capture length** (what the
  player loops), not Music's full-track duration.
- No new native binary. Detection is pure `osascript`; pause/resume is
  orchestrated in Python by segmenting + concatenating.

## Detection: AppleScript polling (decided)

A background thread polls Music once per second via `osascript`. The script is
guarded so it **never launches Music**:

```applescript
if application "Music" is running then
  tell application "Music"
    set st to (player state as text)
    if st is "stopped" then
      return "stopped"
    else
      set t to current track      -- playing / paused / fast forwarding / rewinding
      return st & tab & (persistent ID of t) & tab & (name of t) & tab & (artist of t)
    end if
  end tell
else
  return "not running"
end if
```

- **Track identity** is Music's **persistent ID** (stable string), used to detect
  track changes.
- `player state` distinguishes `playing` / `paused` / **`stopped`** — the three
  states the mode treats differently.
- **Permission:** the first Music `osascript` call triggers the one-time macOS
  **Automation** prompt ("SongCoach wants to control Music"). If denied,
  `osascript` fails (rc 1 / error -1743); the mode surfaces a clear
  "grant it in System Settings → Privacy & Security → Automation" message.
- **~1s boundary slop** is accepted: at a track change we may miss up to ~1s of
  the new song and catch a sliver of it in the old capture. Fine for practice
  material.

The watcher parses each line into a `MusicState`:

```python
@dataclass(frozen=True)
class MusicState:
    state: str            # "playing" | "paused" | "stopped" | "closed"
    track_id: str | None  # persistent ID, or None when not playing/paused
    name: str | None
    artist: str | None
```

Parse normalization (`parse_music_line(raw: str) -> MusicState`, a pure function
unit-tested without macOS):
- `"not running"` → `state="closed"`, no track.
- `"stopped"` → `state="stopped"`, no track.
- `fast forwarding` / `rewinding` → normalized to `state="playing"` (they carry a
  current track, so they must not read as a track change).
- `playing` / `paused` → as-is, with the track fields.
- Any unparseable line → `state="closed"` (fail safe to ARMED, never a false
  capture).

## State machine (`apple_music/session.py`)

The controller consumes `MusicState` samples (one per poll) and diffs against the
previous sample to drive capture. It holds the mode's lifecycle and reuses
`recording.py`'s lock so **manual capture and AM mode are mutually exclusive**.

States: `ARMED` (mode on, nothing capturing), `CAPTURING` (a song's segment is
recording), `PAUSED` (a song's job is open, no segment recording).

Transitions (given previous state `prev` and new sample `s`):

| From | Event | Action | To |
|------|-------|--------|----|
| ARMED | `playing`, new track | begin song job (metadata = name/artist), start segment | CAPTURING |
| CAPTURING | `paused` (same track) | finalize current segment (dead-air not recorded) | PAUSED |
| PAUSED | `playing`, same track | start a new segment on the same job | CAPTURING |
| CAPTURING | `playing`, **different** track | finalize+dispatch current song, begin new song | CAPTURING |
| PAUSED | `playing`, **different** track | finalize+dispatch current song, begin new song | CAPTURING |
| CAPTURING/PAUSED | `stopped` or `closed` | finalize+dispatch current song | ARMED |
| any | **Stop button** | finalize+dispatch current song (if any), stop watcher | (mode ends) |

- **Finalize + dispatch:** `SegmentedRecorder.finish()` concatenates the song's
  segments into `capture.m4a`, the job is stamped (`duration`, `status=queued`)
  and enqueued to the serial stem queue — **unless** the concatenated song is
  shorter than `APPLE_MUSIC_MIN_SONG_SECONDS` (default **5**), in which case the
  job + its capture dir are discarded (skipping through a playlist doesn't spam
  the queue with 1–2s slivers).
- **Begin song:** creates a `recording`/`queued`-style Job with the Music
  metadata (title = name, artist), starts a `SegmentedRecorder`, and fires a
  **best-effort cover-artwork fetch** (see below) — none of which blocks capture.
- Entering the mode **while a song is already playing** begins capturing that
  song mid-way (ARMED→CAPTURING on the first poll), rather than waiting for the
  next track.
- The controller runs its diff/actions on the watcher's thread (single-threaded
  per session), so there's no intra-session locking beyond the shared
  `recording` guard.

## Cover artwork from Apple Music (`apple_music/artwork.py`)

At **begin-song**, a best-effort, off-thread attempt to give the job a real
thumbnail (auto-captured songs otherwise get a blank tile):

- An `osascript` exports the current track's artwork to a file:
  ```applescript
  tell application "Music"
    if (count of artworks of current track) is 0 then return "none"
    set d to raw data of artwork 1 of current track   -- original bytes (JPEG/PNG)
  end tell
  set fh to open for access (POSIX file outPath) with write permission
  set eof fh to 0
  write d to fh
  close access fh
  return "ok"
  ```
  written to `recordings/<job_id>/artwork.<ext>`.
- The file is then stored as the job's thumbnail via a new
  `fetch_thumbnails.store_image_from_file(job_id, path)` (mirrors the existing
  `store_image_from_url` — same size guard + `thumbnail_path` write, minus the
  download), so library tiles and the player show the cover art through the
  existing thumbnail path.
- **Best-effort:** no artwork, an `osascript`/Automation error, or a bad file →
  skipped silently (blank tile, as today). Never fails the capture.
- Runs off the watcher thread (own short-lived thread) so a slow `osascript`
  can't stall the poll loop.

## Segmented recorder (`pipeline/segmented_recorder.py`)

Wraps the existing `Recorder` so one song = one or more segments concatenated into
a single gapless `capture.m4a` (pauses remove dead-air):

- `start()` → first segment (`Recorder` → `segments/000.m4a`).
- `pause()` → `stop()` the current segment (finalizes that segment file).
- `resume()` → new segment (`segments/NNN.m4a`).
- `finish() -> RecordingResult` → stop the last segment if running; **concat** all
  `segments/*.m4a` → `capture.m4a` via
  `ffmpeg -f concat -safe 0 -i list.txt -c copy capture.m4a` (segments share
  identical AAC params from the same `syscap`, so stream-copy is valid); on a
  non-zero exit, **fall back** to a re-encode
  (`ffmpeg -f concat ... -c:a aac -b:a 256k`). A single-segment song skips concat
  (just uses/renames the one segment). Returns duration via the existing
  `_probe_duration`.
- Lives under the job's `recordings/<job_id>/` dir (`segments/` subdir), so the
  final `capture.m4a` sits exactly where `process_capture` expects it and the
  retry/rebuild logic already handles it.

## Serial stem queue (`stem_queue.py`)

Replaces the fire-and-forget thread-per-job in `jobs.enqueue_processing`:

- A module-level `queue.Queue[str]` drained by a **single** lazily-started daemon
  worker that runs `process_capture(job_id)` one at a time.
- `jobs.enqueue_processing(job_id)` becomes a thin delegate to
  `stem_queue.enqueue(job_id)`, so **all** stemming (manual + AM) serializes —
  capture/UI never block, but Demucs runs serially (no CPU/RAM thrash).
- Jobs remain `queued` until the worker picks them up → `processing` → `done`
  (unchanged transitions inside `process_capture`).
- The queue survives for the process lifetime; it is not persisted (consistent
  with today — an interrupted job is already handled by the resume/rebuild path).

## API (`routes/api.py`)

- `POST /api/apple-music/start` → start the mode. 409 if a manual recording is in
  progress or the mode is already running; 200 with initial status otherwise.
  (Does not require Music to be running — starts ARMED.)
- `POST /api/apple-music/stop` → finalize the current song (if any) + dispatch,
  stop the watcher, exit the mode. Returns the final status.
- `GET /api/apple-music/status` → `{active, phase, current: {name, artist}|null,
  captured: [{job_id, title, artist}], permission_error: bool}` for the panel to
  poll (phase ∈ `armed|capturing|paused`).

## Frontend

- A **third mode card** on the landing picker ("Apple Music", with a grayscale
  glyph in the existing card style), alongside YouTube / System audio.
- Selecting it shows an **Apple Music session panel** (in the shared `#flow`
  area, with the existing Back control, disabled while the mode is active):
  - A **Start Apple Music mode** button.
  - Once active: a live status line — `Waiting for Apple Music…` /
    `● Capturing: <song> — <artist>` / `❚❚ Paused` — driven by polling
    `/api/apple-music/status`.
  - A growing **list of songs sent to the queue this session** (title · artist),
    newest first.
  - A **Stop** button (ends the mode).
  - If `permission_error`, a note on granting Automation access.
- `library.js`/`app.js` untouched conceptually; new logic in a small
  `apple-music.js` (separate file, like `library.js`).

## Error handling

- **Automation denied:** watcher surfaces `permission_error=true`; panel explains
  how to grant it; the mode stays active (ARMED) so granting + retrying works
  without re-entering.
- **Music not running / closed:** ARMED, waiting.
- **`syscap` fails on a segment:** the current song's job is marked `failed`
  (existing `_mark_failed`), the mode continues to the next song.
- **Concat fails (copy → re-encode both fail):** that song's job is marked
  `failed`; mode continues.
- **Mutual exclusion:** manual `recording.start()` and `POST /recordings/start`
  return 409 while AM mode is active; `POST /api/apple-music/start` returns 409
  while a manual recording is active. Enforced via a shared guard in
  `recording.py` (an `_apple_music_active` flag checked alongside `_active`).

## Testing

- **Watcher parse** — `parse_music_line` over playing/paused/stopped/not-running/
  malformed inputs → correct `MusicState` (pure, no macOS).
- **State machine** — feed synthetic `MusicState` sequences to the controller with
  a mocked recorder + queue, asserting the exact begin/pause/resume/
  finalize/dispatch calls for: single song play→stop; play→pause→resume→stop;
  continuous track change (playing A → playing B); mid-song entry; a <5s song is
  discarded (not enqueued); Stop mid-capture finalizes+dispatches.
- **Segmented recorder** — with a fake `ffmpeg`/`Recorder` (or tiny real m4a
  fixtures if available), assert: multi-segment → single `capture.m4a` +
  duration; single-segment shortcut; copy-fail → re-encode fallback path invoked.
- **Artwork store** — `store_image_from_file(job_id, path)` with a small real
  image writes the job thumbnail; a missing/oversized file is skipped without
  error (the `osascript` export itself is mocked / not exercised).
- **Serial stem queue** — enqueue several ids with a monkeypatched
  `process_capture` that records concurrency; assert they run one-at-a-time in
  FIFO order and `enqueue` returns immediately.
- **API** — start/stop/status happy paths; 409 when a manual recording is active;
  status shape.
- Platform: `osascript`/`syscap` are macOS-only and are mocked in tests; the
  watcher's polling loop is guarded and not exercised directly.

## Files touched

- **New**: `songcoach/apple_music/__init__.py`, `apple_music/watcher.py`,
  `apple_music/session.py`, `apple_music/artwork.py`,
  `songcoach/pipeline/segmented_recorder.py`, `songcoach/stem_queue.py`,
  `songcoach/static/js/apple-music.js`, `tests/test_apple_music_watcher.py`,
  `tests/test_apple_music_session.py`, `tests/test_segmented_recorder.py`,
  `tests/test_stem_queue.py`, `tests/test_apple_music_api.py`,
  `tests/test_artwork_store.py`.
- **Edit**: `songcoach/jobs.py` (delegate to `stem_queue`), `songcoach/recording.py`
  (shared AM-active guard + helpers the session uses),
  `songcoach/fetch_thumbnails.py` (`store_image_from_file`),
  `songcoach/routes/api.py` (three endpoints),
  `songcoach/templates/index.html` (third card + panel),
  `songcoach/static/css/styles.css` (panel styles), `README.md` (roadmap + a
  short "Apple Music mode" note).
