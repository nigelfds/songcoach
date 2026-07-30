# Soft-delete a library item — design

**Date:** 2026-07-30
**Status:** Approved, ready for plan

## Goal

Let the user delete a recording from the library, from the player view. Deletion
is a **soft delete**: the item's `meta.json` is marked `"deleted": true` and the
DB index is refreshed so it disappears from the library — but the actual files on
disk (stems, thumbnail, capture) are **never touched**.

## Non-goals

- No hard delete / file removal. No "trash" UI or undelete flow (undelete = flip
  the flag in `meta.json` by hand; out of scope).
- No bulk delete. One item at a time, from its player page.
- No schema-version bump (the `deleted` key is additive/optional).
- No `deleted` column on the `Job` model — deleted items are simply excluded from
  the DB, so the model is unchanged.

## Soft-delete flag

The item's sidecar `meta.json` gains an optional key:

```json
{ ..., "deleted": true }
```

Only this key is added. The stem mp3s, `thumbnail.jpg`, and any `recordings/`
capture are left exactly as they are. The DB is the disposable index (rebuilt
from disk), so once `rebuild()` skips deleted items, the recording is gone from
the library, and its player URL 404s — while the bytes remain recoverable on
disk.

## `metadata.mark_deleted(job_id) -> bool`

New helper in `songcoach/metadata.py`, next to `write_meta` / `read_meta`:

- Resolve `meta_path(job_id)`. If it doesn't exist, return `False`.
- Read the JSON, set `data["deleted"] = True`, write it back atomically (temp
  file + `os.replace`, mirroring `write_meta`).
- Return `True`.

It patches the file directly (does not go through `to_dict`/`Job`), so it works
even though there's no `deleted` attribute on the model.

## `rebuild()` skips deleted items

In `songcoach/rebuild.py`, the meta-scan loop reads each
`jobs/<id>/meta.json`. After `read_meta`, add:

```python
if data.get("deleted"):
    continue   # soft-deleted → not indexed, disappears from the library
```

No `Job` row is created for a deleted item. Consequences (all desired):
- It's absent from `GET /api/jobs`, the landing library, and the player (the
  player route does `session.get(Job, id)` → `None` → 404 `not_found.html`).
- The orphan-capture scan (`_index_orphan_captures`) is unaffected: a deleted
  item is a finished job whose `recordings/<id>` capture was already reclaimed by
  `process_capture`, so there's no orphan capture to resurface. (Even if one
  lingered, that's a separate edge outside this feature.)

## Endpoint — `DELETE /api/jobs/{job_id}`

In `songcoach/routes/api.py`:

1. Load the `Job` from the DB. If `None` → **404** ("Job not found").
2. **Guard:** allow deletion only for **terminal** items — `job.status not in
   (JobStatus.done, JobStatus.failed)` → **409** ("Can't delete while it's still
   processing."). Reason: while a job is `recording`/`queued`/`separating`/
   `uploading`, `process_capture` still calls `metadata.write_meta(job)` on
   completion, which would overwrite the `deleted` flag — a race. Terminal
   (`done`/`failed`) items have no such writer.
3. `metadata.mark_deleted(job_id)` — if it returns `False` (no sidecar on disk),
   **404**.
4. **Refresh the DB:** `rebuild(reset=True)` — reindexes from disk, now excluding
   the deleted item.
5. Return **204 No Content**.

(REST-style `DELETE`; the front end calls it with `method: "DELETE"`.)

## Player UI

`songcoach/templates/player.html` — add a delete button to `.console__controls`,
alongside the existing help (`?`) and edit (`✎`) `.icon-btn`s:

```html
<button id="delete-open" class="icon-btn" type="button"
        title="Delete recording" aria-label="Delete recording">🗑</button>
```

`songcoach/static/js/player.js` — wire it (near the edit/help wiring):

```javascript
document.getElementById("delete-open").addEventListener("click", async () => {
  if (!confirm("Delete this recording? It's removed from your library. " +
               "The audio files stay on disk.")) return;
  const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (res.status === 204) { location.href = "/"; return; }
  const d = await res.json().catch(() => ({}));
  alert(d.detail || "Could not delete this recording.");
});
```

On success it returns to the library (`/`). A 409/404 surfaces the server's
message. (Uses the same `confirm()` idiom as the library import flow.)

No new CSS — it reuses `.icon-btn`.

## Testing (existing pytest harness with tmp `data/`)

- **rebuild skips deleted** — write two sidecars (one `deleted:true`, one normal)
  via `metadata` helpers; `rebuild(reset=True)`; assert only the normal job has a
  `Job` row, the deleted one does not.
- **`mark_deleted`** — writes `deleted:true` into an existing sidecar and returns
  `True`; returns `False` for a job with no sidecar; leaves other keys intact.
- **DELETE endpoint** (TestClient):
  - happy path on a `done` job → 204; the job is gone from the DB after; the
    sidecar has `deleted:true`; **the stem files still exist on disk** (soft).
  - 404 for an unknown id.
  - 409 for a job in `separating` (non-terminal) status — sidecar NOT modified.
  - after delete, `GET /jobs/{id}` (player page) returns 404.

## Files touched

- **Edit**: `songcoach/metadata.py` (`mark_deleted`), `songcoach/rebuild.py` (skip
  `deleted`), `songcoach/routes/api.py` (`DELETE /api/jobs/{id}`),
  `songcoach/templates/player.html` (delete button), `songcoach/static/js/player.js`
  (wiring).
- **New tests**: `tests/test_delete_recording.py` (+ a rebuild-skips-deleted case,
  which may live in `tests/test_rebuild_orphans.py` or the new file).
