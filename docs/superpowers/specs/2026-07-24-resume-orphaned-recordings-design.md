# Resume stemming from orphaned recordings

**Date:** 2026-07-24
**Status:** Approved (design, revised)

> **Revision note:** An earlier draft proposed a new `capture.json` sidecar in
> `recordings/`. Investigation showed the durable metadata already lives in
> `jobs/{id}/meta.json` (the existing sidecar), so that parallel file was redundant.
> This revision reuses `meta.json` and fixes a latent bug (failed jobs resurrect as
> stuck `queued`).

## Problem

When stemming fails (or the app quits mid-stem), the captured audio is not lost —
`recording.stop()` has already written `recordings/{id}/capture.m4a`, and
`process_capture()` only deletes it on the *success* path. So the audio survives. But
there is no way to re-stem it: the user must re-record the whole track.

What happens to the metadata across a restart is the crux. `main.py` runs
`rebuild(reset=True)` on every launch — it drops all rows and re-indexes from
`jobs/{id}/meta.json` sidecars on disk. Two gaps make a failed job come back wrong:

1. **`meta.json` is only written on success** (`process.py`) or as a side effect of the
   parallel **thumbnail fetch** (which stamps whatever status was current at the time,
   e.g. `queued`). A failed job therefore has either *no* sidecar (non-YouTube capture,
   so no thumbnail fetch) or a **stale `queued` snapshot**.
2. **The sidecar schema has no `error` field** — `metadata.to_dict()` never writes one,
   though `rebuild._job_from_meta` already *reads* `data.get("error")`.

Net effect on restart: a failed job either **vanishes** (no sidecar) or **resurrects as a
stuck `queued` job** (stale sidecar) — while its `capture.m4a` sits orphaned in
`recordings/`, unreachable.

Observed fixture: `bddaf9401b7e41e5841165a4c9c3e6ee` ("Why'd You Only Call Me When You're
High?", Arctic Monkeys) failed with `No module named 'numpy.core.multiarray'`; its
`jobs/…/meta.json` carried `status: "queued"` and no error until hand-patched.

## Goal

Let the user re-stem an existing capture instead of re-recording, via a manual
**Retry** action, and have such captures remain discoverable and correctly labeled
across app restarts.

Non-goals (YAGNI): auto-resume on startup; a distinct `needs_stemming` status; a separate
"interrupted recordings" UI section. Retry is manual and reuses the existing `failed`
state.

## Design

The durable metadata is `jobs/{id}/meta.json`; the durable audio is
`recordings/{id}/capture.m4a`. Make failure persist accurately to the sidecar, then let
`rebuild()` (which already reads sidecars) surface it as a retryable `failed` job.

### 1. Persist the error in the sidecar — `metadata.py`

Add `"error": job.error` to `to_dict()`. `rebuild._job_from_meta` already consumes it, so
this is the only schema change needed. Bump nothing else (`schema_version` stays 1 — the
field is additive and older sidecars simply omit it).

### 2. Write the sidecar on failure — `process.py`

In `_fail()`, after setting `status=failed` + `error`, call `metadata.write_meta(job)`
(which creates `jobs/{id}/` if absent). This guarantees **every** failed job — YouTube or
not — leaves an accurate, restart-durable sidecar, and fixes the "resurrects as stuck
`queued`" bug at its root.

### 3. Rebuild surfaces orphans — `rebuild.py`

- **Primary (already works):** the `jobs/*/meta.json` scan now finds failed jobs with
  `status=failed`, zero tracks (no stems on disk), and the error message. They list as
  proper failed jobs. No change required beyond steps 1–2.
- **Fallback (hardening):** after the `jobs/` scan, also scan `recordings/*/capture.m4a`
  for captures whose id was **not** indexed from `jobs/` (a hard crash before `_fail`
  ran, or a legacy capture). Index each as a `failed` job with a fallback title
  (`"Untitled recording {date}"` from the file mtime). Skip ids already indexed so a
  published/failed `jobs/` entry always wins.

### 4. Retry endpoint + resumable flag — `routes/api.py`

- `POST /api/jobs/{id}/retry`: guards — job exists; status not currently
  `recording`/`separating`/`queued`; `capture_dir(id)/capture.m4a` exists (else `409`).
  Resets the row (`status=queued`, `progress=10`, `error=None`), commits,
  `jobs.enqueue_processing(id)`, returns the serialized job. Normal status polling
  resumes from there.
- Add `resumable: bool` to `JobOut` — true when the job is `failed` and its
  `capture.m4a` still exists — so the UI shows Retry only when a re-stem can actually
  succeed.

### 5. UI — Retry button

A "Retry stemming" button on `resumable` failed jobs, in the library list and on the
failure screen (next to the existing "start over"). Wires to the endpoint, then polls
status as usual.

## Error handling

- Retry with missing `capture.m4a` → `409 "This recording is no longer available."`
- Retry while a recording is in progress, or the job is already re-queued → `409`.
- `write_meta` failure in `_fail` → log a warning; the DB row is still marked failed
  (in-session correctness preserved; only cross-restart durability is at risk).
- Sidecar read errors during rebuild → skip with a warning, don't abort (existing
  behavior).

## Data flow

```
record → stop() writes capture.m4a, enqueues process_capture
  success → publishes jobs/{id}/ + meta.json(done), rmtree(recordings/{id})
  failure → _fail: row=failed, writes jobs/{id}/meta.json(failed, error);
            capture.m4a remains in recordings/{id}
restart → rebuild wipes DB, re-indexes jobs/ (done + failed) [+ recordings/ fallback]
click Retry → endpoint re-enqueues → process_capture reruns from the same capture.m4a
```

## Testing

Automated:
- `to_dict()` includes `error`; round-trips through `write_meta`/`read_meta`.
- `_fail()` writes a `failed` sidecar (creating `jobs/{id}/` when absent).
- `rebuild()`: a failed sidecar with no stems resurrects as one `failed` job with its
  error; the `recordings/` fallback indexes a sidecar-less capture with a fallback title
  and does **not** duplicate an id already present in `jobs/`.
- Retry endpoint: resets status + enqueues on a resumable job; `409` when `capture.m4a`
  is gone. `resumable` flag reflects capture presence.
- `process_capture` success removes `recordings/{id}` (audio gone, no orphan left).

Manual:
- Use the existing real fixture
  `jobs/bddaf9401b7e41e5841165a4c9c3e6ee/` (+ its `recordings/…/capture.m4a`), already
  patched to `status: failed` with the numpy error. After `rebuild()` it must appear in
  the library as a failed job showing the error, with a working Retry button; retrying
  runs stemming to completion and cleans up the `recordings/` folder.

## Note: dev `.env` vs. frozen paths (test-environment gotcha)

`paths.py` sends frozen-app data to `~/Library/Application Support/SongCoach/`, but
`config.Settings` reads `.env` (via `pydantic-settings`) from the launch directory, and
the repo `.env` sets `LOCAL_STORAGE_DIR=./data` / `DATABASE_URL=sqlite:///./songcoach.db`.
So the **built** app, when launched from the repo, writes its DB and recordings to
`./data` (only `setup_runtime()`'s torch-cache seeding, which ignores settings, lands in
Application Support). This is why the fixture lives under `./data`. Implication for this
work: **test the built app from the repo directory** so it sees the `./data` fixture. (A
separate hardening — making the frozen build ignore a stray `.env` — is out of scope
here but worth a follow-up so packaged behavior is deterministic.)
