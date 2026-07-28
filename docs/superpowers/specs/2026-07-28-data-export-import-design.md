# Data export / import — design

**Date:** 2026-07-28
**Status:** Approved, ready for plan

## Goal

Let a user take their library to another machine. SongCoach runs entirely off the
`data/` folder — `data/jobs/<id>/` (stems + `thumbnail.jpg` + `meta.json`) and
`data/recordings/<id>/capture.m4a` (in-progress/orphan captures) are the source of
truth; `songcoach.db` is a disposable index rebuilt from disk on launch. So "take
your data with you" reduces to: **move `data/`.**

Two operations, both from the web UI (the packaged `.app` has no terminal):

- **Export** — download the whole `data/` folder as one `.zip`.
- **Import** — pick a `.zip` and merge it into the current library with
  **`cp -rf` semantics**: overwrite files on conflict, never delete anything
  already present, then rebuild the cache.

## Non-goals

- No CLI (the backend module is written so one could be added later; not now).
- No selective export (single recording). Whole library only.
- No cloud / sync / remote transfer. The `.zip` is the transport; the user moves
  it however they like (AirDrop, USB, etc.).
- The DB is never exported or imported — it is always rebuilt from disk.

## Archive format

The archive mirrors the `data/` folder verbatim, so it's simple and
hand-inspectable:

```
SongCoach-export-YYYYMMDD.zip
├── songcoach-export.json      # tiny manifest (see below)
├── jobs/<id>/original.mp3, drums.mp3, no_drums.mp3, thumbnail.jpg, meta.json
└── recordings/<id>/capture.m4a
```

Extraction base is `data_dir()`, so a member `jobs/abc/meta.json` lands exactly
where it belongs.

**Manifest** (`songcoach-export.json`, at archive root):

```json
{ "app": "SongCoach", "schema": 1, "created_at": "<ISO-8601>", "jobs": <int> }
```

Used for a friendly import confirmation and a schema-version check. Its **absence
is tolerated** — a plain `zip -r data.zip jobs recordings` still imports. (Since
`Date.now()`-style calls are fine in the backend, `created_at` is stamped at
export time; if unavailable, omit the field rather than fail.)

## New module: `songcoach/archive.py`

Holds all the zip/unzip + safety logic, independent of FastAPI so it's unit-
testable and CLI-able later. Reads `settings.local_storage_dir` for the data root.

### `build_export(dest_zip: Path) -> int`

- Writes a `.zip` to `dest_zip` using **`ZIP_STORED` (no compression)** — the mp3s
  are already compressed, so this is essentially a fast disk copy and keeps CPU/
  time down on 200 MB+ libraries.
- Includes everything under `data/jobs/` and `data/recordings/` plus the manifest
  at the root. Skips junk (`.DS_Store`).
- Returns the number of `jobs/<id>/` recordings included (for the manifest/log).

### `import_archive(zip_path: Path) -> ImportResult`

`ImportResult` = `{added: int, updated: int}` (jobs added vs. overwritten).

1. Open as a zip; a non-zip / corrupt file raises `ArchiveError` (→ 422).
2. **Zip-slip guard**: for every member, resolve its target against
   `data_dir().resolve()` and require the resolved path stays inside. Absolute
   paths and `..` escapes are skipped (logged), never written.
3. **Whitelist**: only members under `jobs/` and `recordings/` are extracted.
   Anything else (including the manifest, which is read separately) is ignored —
   defense in depth beyond the slip guard.
4. **Overlay (`cp -rf`)**: extract each file, overwriting on conflict, **never
   deleting** anything already on disk. Net effect is the union of both libraries;
   the archive wins on same-path collisions; nothing you already had is lost.
   - Edge case, intentional: if the archive's copy of a job is missing a stem you
     already have, the file-level overlay leaves your extra stem in place. This is
     consistent — `rebuild()` derives tracks from the files *actually present*, so
     the result is a coherent union, not a broken job.
5. Run `rebuild(reset=True)` to reindex the merged tree.
6. Return the counts (added = new job ids, updated = job ids that already existed).

## API endpoints (`songcoach/routes/api.py`)

- `GET /api/export`
  - 409 if `recording.is_recording()` (don't snapshot mid-capture).
  - Builds the zip in a temp file, returns `FileResponse` with
    `filename="SongCoach-export-YYYYMMDD.zip"` and a `BackgroundTask` that deletes
    the temp file after the response is sent.
- `POST /api/import` (multipart file upload, field `file`)
  - 409 if `recording.is_recording()` (don't mutate `data/` mid-capture).
  - 422 if the upload isn't a valid archive (`ArchiveError`).
  - Saves the upload to a temp file, calls `import_archive`, returns
    `{added, updated}`.

Both write to the scratchpad/OS temp dir, cleaned up after use.

## Front end (`index.html` + `static/js/app.js` + `static/css/styles.css`)

- Two buttons in the landing header: **Export** and **Import**.
- **Export**: `window.location = '/api/export'` — native browser download (free
  progress bar). Button disabled while a recording is in progress.
- **Import**: a hidden `<input type="file" accept=".zip">`. On file pick:
  1. Confirm: *"Merge these recordings into your library? Any with the same ID
     will be overwritten."*
  2. Show a spinner/overlay: *"Importing… this can take a minute."*
  3. `POST` the file as multipart to `/api/import`.
  4. On success: toast *"Imported N recordings"* (added + updated), then **reload
     the page** so the merged library renders.
  5. On 409: message *"Stop the current recording first."* On 422: *"That doesn't
     look like a SongCoach export."*

## Concurrency & safety

- Single-user app, so no locking beyond the `is_recording()` gate on both
  endpoints. Import mutates `data/` and rebuilds the shared DB; the page reload
  after import refreshes the in-memory view.
- Temp files (export zip, uploaded import zip) are always cleaned up.
- Zip-slip guard + member whitelist mean a malicious or malformed archive can only
  ever write inside `data/jobs/` and `data/recordings/`.

## Tests (existing pytest harness with a tmp `data/` via the `storage_dir` fixture)

- **Round-trip**: `build_export` then `import_archive` into an empty data dir
  reproduces the library (job dirs + files present, `rebuild` sees them).
- **Merge / `cp -rf`**: a pre-existing unrelated job survives the import; a
  same-ID job's file is overwritten by the archive's version; the archive's copy
  missing a stem the local job has → local stem remains (union), and `rebuild`
  reports a coherent job.
- **Counts**: `added` / `updated` reflect new vs. pre-existing ids.
- **Zip-slip**: an archive member with an absolute path or `../` escape is
  rejected and nothing is written outside `data_dir()`.
- **Whitelist**: a member outside `jobs/`/`recordings/` is ignored.
- **Manifest-less zip** (plain `zip -r`) still imports.
- **Non-zip / corrupt upload** raises `ArchiveError` (→ 422 at the route).
- **409 while recording**: both endpoints reject when `is_recording()` is patched
  true.

## Files touched

- **New**: `songcoach/archive.py`, `tests/test_archive.py`.
- **Edit**: `songcoach/routes/api.py` (two endpoints), `songcoach/templates/index.html`
  (two buttons + hidden input + overlay), `songcoach/static/js/app.js` (wiring),
  `songcoach/static/css/styles.css` (button + overlay styles).
- **Docs**: README "Using it" gets a short "Move your library to another Mac"
  note; roadmap item ticked.
