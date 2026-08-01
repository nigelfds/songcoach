# Waveform markers — design

**Date:** 2026-08-01
**Status:** Approved, ready for plan

## Goal

Let a drummer annotate a recording's waveforms with named **markers** — "guitar
solo", "fill", "transition", etc. — so they can find the spot fast. A marker is a
vertical line spanning **all three synced waveforms (including the REF track)** at
a timestamp, with a small **"i"** badge. The name is hidden until the user clicks
the badge. Markers persist in the recording's `meta.json` sidecar (the app's
source of truth).

## Non-goals

- No marker-to-marker looping / navigation (markers don't drive playback).
- No per-stem markers — a marker spans all rows at one time.
- No colour/category system — just a name.
- No `Job`-model column for markers (they live only in the sidecar, like the
  soft-delete `deleted` flag).

## Data model

`meta.json` gains an optional array:

```json
"markers": [
  { "id": "b1f3…", "time": 84.6, "name": "Guitar solo" }
]
```

- `id`: stable string (client `crypto.randomUUID()`), for edit/delete.
- `time`: seconds from the start (float ≥ 0).
- `name`: string (may be empty; trimmed; capped, see validation).

Markers live **only in the sidecar**. The `Job` model / DB / `JobOut` are
unchanged. `rebuild()` ignores markers (it reads `meta.json` but builds no marker
state — the player reads them from the sidecar via the markers endpoint).

### `write_meta` must preserve markers

`write_meta(job)` rebuilds the sidecar from `to_dict(job)`, which does **not**
include markers. It already preserves an existing `deleted` flag; extend that to
also preserve `markers`, so an edit (`PATCH`) or async thumbnail refresh can't
wipe a recording's markers. (Generalize the existing preserve step to carry a set
of sidecar-only keys — `deleted`, `markers`.)

## Endpoints (`songcoach/routes/api.py`)

- `GET /api/jobs/{id}/markers` → `{ "markers": [...] }` read from the sidecar.
  404 if the job has no sidecar. The player fetches this once on load.
- `PUT /api/jobs/{id}/markers` with body `{ "markers": [...] }`:
  - Validate: a list (cap **200**); each item `{id: str, time: number ≥ 0,
    name: str}`; `name` trimmed and capped (**120** chars); invalid payload →
    **422**.
  - Write the array into the sidecar (a new `metadata.write_markers(job_id,
    markers) -> bool`, atomic tmp+replace, preserving the rest of the file);
    404 if no sidecar. Returns `{ "markers": [...] }` (the stored value).
  - Whole-array replace on every change (add / rename / delete) — simple, atomic,
    and markers are tiny.

`metadata` also gets `read_markers(job_id) -> list` (returns `[]` if none).

## Player: marker mode + placement

A **flag icon** button (`#marker-open`, inline SVG) in `.console__controls`, next
to `?` (`#help-open`) and `✎` (`#edit-open`).

- Clicking it **toggles marker mode**: the icon lights up (an `is-on` class /
  `aria-pressed`), and the waveform cursor becomes a crosshair.
- Placement uses a transparent **capture overlay** (`#marker-capture`) over the
  waveform stack (same rect as `phGeom`): `pointer-events:none` normally,
  `auto` while armed. So while marker mode is on it intercepts the click and the
  hover **without disabling** WaveSurfer's click-to-seek or the Regions
  loop-drag (those simply don't receive events while armed).
- **Click** on the overlay → time via the inverse of the playhead geometry:
  `time = clamp((clickX − phGeom.left) / phGeom.width × duration)`. Create a
  marker `{id: crypto.randomUUID(), time, name: ""}`, render it, and open the
  modal to name it (see below). Marker mode is **sticky** — it stays on so
  several can be dropped; toggling the icon exits.
- **Hover tooltip** (placement aid): on `mousemove` over the capture overlay,
  show a small tooltip (`#marker-tip`) that **follows the pointer** and displays
  the time at the cursor x (`mm:ss.d`). Hide it on mouse-leave and whenever
  marker mode turns off.

## Player: rendering (reuse `phGeom`)

Markers render as overlays inside `#strips`, mirroring the shared `#playhead`:

- A `#marker-layer` div holds one element per marker: a vertical **line** at
  `x = phGeom.left + time/duration × phGeom.width`, with `top = phGeom.top` and
  `height = phGeom.height` — so it spans **all three rows including REF**.
- Each marker carries a small **"i" badge** at the top of its line. The badge is
  clickable (`pointer-events:auto`) even though the layer is `none`; clicking it
  opens the modal for that marker (edit/delete). The **name is not shown on the
  waveform** — only the line + badge.
- `layoutMarkers()` recomputes all marker x/top/height; it's called wherever
  `layoutPlayhead()` is (initial layout + the `resize` handler).

## Player: the modal (reuse `.edit-overlay` / `.edit-card`)

A new dialog `#marker-overlay` in the `.edit-overlay`/`.edit-card` idiom, opened
both when **placing** a marker and when **clicking an existing marker's "i"**:

- Shows the **timestamp** read-only (`mm:ss.d`).
- A **name** text input (focused on open; prefilled when editing).
- Actions: **Delete** (removes this marker), **Cancel**, **Save** (stores the
  name). Save / Delete both persist the whole array via `PUT`.
- **Cancel on a brand-new marker with no saved name → discard it** (don't persist
  a nameless placeholder). Cancel on an existing marker leaves it unchanged.
- Escape / clicking the backdrop = Cancel (matching the edit/help overlays).

## Persistence flow

- On player load: `GET …/markers` → render them.
- On Save/Delete in the modal: update the in-memory array, re-render, and
  `PUT …/markers` with the full array. On a failed PUT, surface an error in the
  modal and keep it open (don't lose the edit).

## Testing

**Backend (pytest):**
- `metadata.read_markers` / `write_markers` round-trip; `write_markers` returns
  False with no sidecar and preserves other keys.
- `write_meta` preserves an existing `markers` array (the resurrection-class
  guard, mirroring the `deleted` test).
- `GET /api/jobs/{id}/markers` (200 with array, 404 no sidecar); `PUT` (200 +
  stored; 422 on a bad payload — non-list, missing/negative time, over-long
  name/over-cap count; 404 no sidecar).

**Frontend (Playwright):**
- Toggle marker mode (icon lit); hovering the waveform shows the time tooltip.
- Click the waveform → a marker line spans all rows + an "i" badge; the modal
  shows the timestamp; enter a name, Save.
- Reload → the marker persists (line + badge) — proves the sidecar round-trip.
- Click the "i" → modal shows the name; Delete → the marker is gone (and gone
  after reload).
- A metadata edit (PATCH title) does not wipe the markers.

## Files touched

- **Edit**: `songcoach/metadata.py` (`read_markers`, `write_markers`; generalize
  `write_meta`'s preserve to include `markers`), `songcoach/routes/api.py`
  (GET/PUT markers), `songcoach/templates/player.html` (marker icon, modal,
  capture overlay + marker layer + tooltip nodes), `songcoach/static/js/player.js`
  (mode toggle, placement, tooltip, rendering, modal, persistence),
  `songcoach/static/css/styles.css` (marker line/badge, tooltip, lit icon,
  crosshair).
- **New tests**: `tests/test_markers.py`.
