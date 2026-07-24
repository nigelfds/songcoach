# Resume stemming from orphaned recordings

**Date:** 2026-07-24
**Status:** Approved (design)

## Problem

When stemming fails (or the app quits mid-stem), the captured audio is not lost —
`recording.stop()` has already written `recordings/{job_id}/capture.m4a`, and
`process_capture()` only deletes it on the *success* path. So the audio survives on
disk. But there is no way to re-stem it: the user must re-record the whole track.

Two things make the orphan effectively invisible:

1. **All capture metadata (title, artist, YouTube URL, duration) lives only in the
   SQLite `Job` row.** Nothing is written next to `capture.m4a`.
2. **`main.py` runs `rebuild(reset=True)` on every launch**, which drops all rows and
   re-indexes *only* from `jobs/` (published stems). A `failed` job's row is therefore
   wiped on the next start, leaving `capture.m4a` as a fully orphaned file — no title,
   no artist, no duration, just audio in a folder named by job-id.

Observed: a real failed run left `data/recordings/bddaf9401b7e41e5841165a4c9c3e6ee/capture.m4a`
on disk with nothing in the library pointing to it.

## Goal

Let the user re-stem an existing capture instead of re-recording, via a manual
**Retry** action, and have such captures remain discoverable across app restarts.

Non-goals (YAGNI): auto-resume on startup, a distinct `needs_stemming` job status, a
separate "interrupted recordings" UI section. Retry is manual and reuses the existing
`failed` state.

## Disk contract (the core idea)

**The presence of `recordings/{id}/capture.m4a` with no published `jobs/{id}/` output
*is* a resumable recording.** The audio file — not a DB row, not the sidecar — is the
durable trigger. This is what survives the startup `rebuild(reset=True)`.

### `capture.json` sidecar

Written next to `capture.m4a` in `recordings/{id}/`, carrying the metadata that
otherwise only lived in the DB row:

```json
{
  "id": "…",
  "title": "…",
  "artist": "…",
  "youtube_url": "…",
  "duration_seconds": 214.3,
  "created_at": "…",
  "error": "<last failure message, if any>"
}
```

- Written at `recording.stop()`, once the capture is finalized and metadata stamped,
  before enqueuing separation.
- **Best-effort:** a write failure logs a warning but never sinks the recording.
- **Metadata enrichment, not the trigger.** A capture with no sidecar (the existing
  `bddaf94…` one, and any pre-feature recordings) is still resumable — it falls back to
  a default title `"Untitled recording {date}"` derived from the file's mtime.
- On a successful stem, the existing `shutil.rmtree(recordings/{id})` cleanup removes
  both the audio and the sidecar — no orphan left behind.

An orphan is surfaced in the library as a **`failed`** job with an error message such as
*"Stemming didn't finish — retry to resume."* (or the real last error, if the sidecar
has one).

## Code touchpoints

### 1. `recording.stop()` writes the sidecar — `recording.py`

After the capture is finalized and the job row stamped (title/duration), write
`capture.json` into `capture_dir(job_id)` before `jobs.enqueue_processing(job_id)`.
Wrapped best-effort (log-and-continue on failure).

Add sidecar I/O helpers alongside the existing job-sidecar helpers in `metadata.py`:
- `write_capture_meta(job, capture_dir)` — serialize the fields above.
- `read_capture_meta(capture_dir)` — parse, tolerant of a missing/broken file.

### 2. `rebuild()` indexes orphans — `rebuild.py`

After the existing `jobs/` scan:
1. Collect the set of job-ids indexed from `jobs/`.
2. Scan `recordings/*/capture.m4a`.
3. For each capture whose id is **not** already indexed from `jobs/`, build a `failed`
   Job from its `capture.json` (or fallback title if absent): `progress=0`,
   `error` = stored error or the default resume message, `created_at` from sidecar/mtime,
   `duration_seconds` from sidecar.
4. `session.merge()` it (same PK-keyed upsert already used), so a `done` job from
   `jobs/` always wins and orphans are never double-listed.

Sidecar read errors → skip that orphan with a warning; never abort the rebuild (mirrors
the existing `read_meta` handling).

### 3. Retry endpoint + button

**`POST /api/jobs/{id}/retry`** (`routes/api.py`):
- Guards: job exists; status not currently `recording`/`separating`/`queued`;
  `capture_dir(id)/capture.m4a` exists — else `409`.
- Resets the row: `status=queued`, `progress=10`, `error=None`; commit.
- Calls `jobs.enqueue_processing(id)`; returns the serialized job.
- Normal status polling takes over from there.

**UI:** a "Retry stemming" button on `failed` jobs — in the library list and on the
failure screen (next to the existing "start over"). Wires to the endpoint, then polls
status as usual.

## Error handling

- Retry with missing `capture.m4a` → `409 "This recording is no longer available."`
- Retry while a recording is in progress, or the job is already re-queued → `409`
  (guards double-clicks / races).
- Sidecar read errors during rebuild → skip with a warning, don't abort.
- Sidecar write failure at `stop()` → warning; recording proceeds normally.

## Data flow

```
record → stop() writes capture.m4a + capture.json, enqueues process_capture
  success → publishes jobs/{id}/, rmtree(recordings/{id})  ← both files gone
  failure → row=failed; capture.m4a + capture.json remain
restart → rebuild wipes DB, re-indexes jobs/ (done) + recordings/ orphans (failed)
click Retry → endpoint re-enqueues → process_capture reruns from the same capture.m4a
```

## Testing

Automated:
- Sidecar write/read roundtrip.
- `rebuild()`: an orphan capture (audio in `recordings/`, nothing in `jobs/`) indexes as
  one `failed` job with its metadata; a capture that also has a `done` `jobs/` output is
  **not** duplicated; a capture with no sidecar still indexes with the fallback title.
- Retry endpoint: resets status + enqueues on a valid orphan; `409` when `capture.m4a`
  is gone.
- `process_capture` success removes both `capture.m4a` and `capture.json`.

Manual:
- Use the existing real orphan `data/recordings/bddaf9401b7e41e5841165a4c9c3e6ee/`
  (a capture with **no** sidecar) to verify the UI: after `rebuild()`, it appears in the
  library as a `failed` job with the fallback title and a working Retry button; retrying
  runs stemming to completion and the orphan folder is cleaned up.
