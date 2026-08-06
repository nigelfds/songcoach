# Vocals stem + library reprocessing — design

**Date:** 2026-08-06
**Status:** Approved, ready for plan

## Goal

Add a **vocals** stem so every song separates into four rows: the full-song
reference, **drums** only, **vocals** only, and **BACKING** (the song with drums
and vocals removed = `bass + other`). Provide two ways to bring existing
recordings up to the new 4-stem model: a **CLI** that reprocesses the whole
library, and a **reprocess button** on the player that does one song. Both call
the same core.

## Non-goals

- No new Demucs model / no bass or "other" as separate user-facing stems (they're
  summed into BACKING). `htdemucs` already yields drums/bass/other/vocals.
- No re-capture — reprocessing runs on the retained `original.mp3`, not the raw
  system-audio capture (which is deleted after the first stemming).
- No change to markers, A–B loop, or the mixer beyond adding rows.

## Separation change (`songcoach/pipeline/separator.py`)

`htdemucs` already produces all four sources; today `separate()` keeps `drums`
and sums the rest into `no_drums`. Change it to extract three stems:

- `drums`   = the `drums` source
- `vocals`  = the `vocals` source
- `backing` = sum of every source except `drums` and `vocals` (= `bass + other`)

`SeparationResult` becomes `{drums_path, vocals_path, backing_path}` (files
`drums.mp3`, `vocals.mp3`, `no_drums_no_vocals.mp3`). Require the model to have
both `drums` and `vocals` sources (raise otherwise). **No model change and no
extra compute** — the full model already runs; we just `save_audio` vocals
separately and sum the remainder differently.

## Track model (`songcoach/models.py`)

`TrackKind` gains two values; the legacy one stays for old sidecars:

```python
class TrackKind(str, enum.Enum):
    original = "original"
    drums = "drums"
    vocals = "vocals"                       # NEW — vocals only
    no_drums_no_vocals = "no_drums_no_vocals"  # NEW — BACKING (bass + other)
    no_drums = "no_drums"                   # LEGACY — pre-reprocess recordings
```

The DB is a disposable index rebuilt from disk, so adding enum values needs no
migration. `rebuild._job_from_meta` iterates `TrackKind` and picks up whichever
`<kind>.mp3` files exist, so a job is 3-track (old) or 4-track (new) purely from
the files present. `metadata.to_dict` already emits `job.tracks` verbatim.

## New captures (`songcoach/pipeline/process.py`)

`process_capture` publishes the new set: `original` (built from the capture via
ffmpeg as today), `drums`, `vocals`, `no_drums_no_vocals`. It no longer writes
`no_drums`.

## Reprocess core (shared by button + CLI)

New `reprocess_job(job_id: str) -> None` in **`songcoach/pipeline/process.py`**
(so it reuses that module's publish/`_fail` helpers; the CLI lives separately in
`songcoach/reprocess.py` to avoid a module-vs-function name clash), mirroring the
publish half of `process_capture` but sourcing from the retained full-song mp3:

1. Load the job; require it is `done` and `jobs/<id>/original.mp3` exists (else
   raise — the caller maps to an error / skips).
2. `status = separating`.
3. Run `separator.separate(original.mp3, tmp)` → drums/vocals/backing.
4. Publish `drums`, `vocals`, `no_drums_no_vocals` into `jobs/<id>/` (via
   `storage.save`), **delete the legacy `no_drums.mp3`** if present, and rebuild
   `job.tracks` to `original + drums + vocals + no_drums_no_vocals`.
5. `status = done`; `metadata.write_meta(job)` — which **preserves `markers`**
   (reprocessing keeps the same timeline/duration, so existing markers stay
   valid) and `deleted`.
6. On failure, `_fail(...)` (existing) marks the job failed with the error.

`original.mp3` is never regenerated — it's both the reprocess source and the REF
track.

## Serial queue dispatch (`songcoach/stem_queue.py`)

The queue currently runs `process_capture` for each id. Generalize it to carry a
task kind so a reprocess runs the reprocess core:

- `enqueue(job_id)` — unchanged (a normal capture → `process_capture`).
- `enqueue_reprocess(job_id)` — enqueues a reprocess task → `reprocess_job`.
- The worker dispatches on the task kind. Everything still runs **one at a time**
  through the single worker (no concurrent Demucs).

## API (`songcoach/routes/api.py`)

`POST /api/jobs/{id}/reprocess`:

- 404 if unknown.
- 409 if `recording.is_recording()`, if `job.status != done`, or if
  `jobs/<id>/original.mp3` is missing ("Nothing to reprocess").
- Else set `status = separating`, `progress = 10`, `stem_queue.enqueue_reprocess(id)`,
  return the serialized job (200). The player polls status and reloads on `done`.

## CLI (`python -m songcoach.reprocess`)

- Rebuilds the index (`rebuild(reset=True)`), then iterates all `done` jobs and
  calls `reprocess_job(job_id)` **synchronously, one at a time** (naturally
  serial; no queue needed in the CLI process; doesn't require the web server).
- **Skips** any job that already has a `vocals` track (i.e. already reprocessed)
  unless `--force` is passed — so re-running is cheap/idempotent.
- Logs per-song progress and a final summary (`N reprocessed, M skipped, K failed`).
- Optional positional arg to limit to one/some job ids (nice-to-have; the plan can
  keep it to "all done jobs" for v1).

## Player (`player.js` / `player.html` / `styles.css`)

- `KINDS` gains **VOCALS** and **BACKING**, legacy `no_drums` kept last:
  ```
  original            FULL SONG   reference mix       (violet, REF)
  drums               DRUMS       the kit, solo       (orange)
  vocals              VOCALS      the voice, solo     (pink/magenta)
  no_drums_no_vocals  BACKING     bass, keys & the rest (teal)
  no_drums (legacy)   NO DRUMS    play along          (teal) — only if present
  ```
  The player already renders only the kinds a job actually has and skips missing
  ones, so 4-stem jobs show 4 rows and un-reprocessed jobs show 3. The stale
  comment "`original == drums + no_drums`" is updated (now `drums + vocals +
  backing`). The mixer/REF/gain logic is already N-stem generic.
- **Reprocess icon** — a circular-arrows `icon-btn` in `.console__controls` next
  to edit/delete (`#reprocess-open`). Click → `confirm("Re-separate this song to
  add a vocals stem? Takes a minute.")` → `POST /api/jobs/{id}/reprocess`. On 200,
  show a "Re-separating…" state and poll `/api/jobs/{id}`; when it returns to
  `done`, `location.reload()` to rebuild the deck with the new stems. On 409/404,
  surface the message.

## Testing

**Backend (pytest, externals mocked — no real Demucs):**
- `separate()` stem split: monkeypatch the model + `apply_model` to return known
  named tensors (`drums/bass/other/vocals`) and `save_audio`; assert `drums`=drums,
  `vocals`=vocals, `backing`=`bass+other`, and three files "saved".
- `reprocess_job`: seed a done job with `original.mp3` + a legacy `no_drums.mp3`
  + a marker; monkeypatch `separator.separate` to drop fake stem files; assert the
  job ends with tracks `{original, drums, vocals, no_drums_no_vocals}`, the legacy
  `no_drums.mp3` is gone, the marker is preserved in `meta.json`, and `original.mp3`
  is untouched.
- `stem_queue.enqueue_reprocess` dispatches to `reprocess_job` (monkeypatch, assert
  serial ordering like the existing queue test).
- `POST /api/jobs/{id}/reprocess`: 200 enqueues (fake queue), 404 unknown, 409 for
  non-done / missing-original / recording-in-progress.
- CLI: with `reprocess_job` monkeypatched, running the CLI reprocesses each done
  job once, skips a job that already has a `vocals` track (unless `--force`), and
  reports counts.

**Frontend (Playwright, seeded real-audio job):**
- A seeded **4-stem** job renders four strips (FULL SONG / DRUMS / VOCALS /
  BACKING); a legacy **3-stem** job renders three.
- The `#reprocess-open` icon is present next to edit/delete; clicking it (accepting
  the confirm) fires `POST …/reprocess` and enters the re-separating state. (The
  actual re-separation is covered at the backend level with a mocked separator;
  the browser test asserts the trigger + state, not a live Demucs run.)

## Files touched

- **Edit**: `songcoach/pipeline/separator.py`, `songcoach/pipeline/process.py`
  (+ `reprocess_job`, or a new `pipeline/reprocess.py`), `songcoach/models.py`
  (TrackKind), `songcoach/stem_queue.py` (reprocess dispatch),
  `songcoach/routes/api.py` (reprocess endpoint), `songcoach/static/js/player.js`
  (KINDS + reprocess button/flow), `songcoach/templates/player.html` (reprocess
  icon), `songcoach/static/css/styles.css` (vocals/backing colours if needed).
- **New**: `songcoach/reprocess.py` (CLI `__main__`),
  `tests/test_vocals_reprocess.py` (+ any split test files the plan prefers).
