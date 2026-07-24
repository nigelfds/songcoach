# Recording mode picker: YouTube vs. system audio

**Date:** 2026-07-24
**Status:** Approved (design)

## Problem

The landing page assumes a YouTube-centric flow (paste link → in-app embed → capture).
But many YouTube videos disable embedding — and YouTube blocks the `watch` page from
*any* iframe — so those can't play in-app at all. There's also no first-class path for
capturing audio from other sources (Apple Music, Spotify, SoundCloud, a local file),
even though `syscap` captures whatever plays on the Mac regardless of source.

## Goal

Start the landing page with a **two-card mode picker**:

1. **Record from YouTube** — the existing embed flow (paste → embed → metadata → capture,
   auto-stop when the video ends).
2. **Record from system audio** — no in-app player; the user plays audio from any app and
   manually starts/stops the capture. Serves as the universal fallback (incl.
   embed-disabled videos).

Both modes capture via `syscap`; the only difference is YouTube mode's in-app embed +
auto-stop. This is a front-end restructure plus one small backend addition (fetch a
user-supplied image URL as the job thumbnail).

## Non-goals (deferred)

- **Persisting the system-audio image URL** (option B). We fetch-and-forget: fetch the
  image once at capture start and store `thumbnail.jpg`; we do NOT save the source URL,
  so there's no later "edit + re-fetch" for system-audio thumbnails. Can add an
  `image_url` column later if needed. No DB/schema change in this work.
- Remembering the last-used mode; the picker always shows first.

## Front-end flow (three client-toggled views)

Structure inside the existing `.hero__panel` (keep the analog-gear aesthetic —
`.rack-screws`, `.tape`, `.chip`, `.btn-rec`). Use a shared metadata + capture block to
avoid duplicating capture logic:

```
#mode-picker  (View 0, shown first)
   two .mode-card buttons: YouTube (play glyph) | System audio (icon collage)

#flow  (Views 1 & 2, hidden until a card is picked)
   ‹ Back            (returns to #mode-picker; DISABLED while recording)
   #yt-chrome        (shown in YouTube mode): yturl loader + #yt-embed
   #sys-chrome       (shown in system mode): explainer text + icon collage
   [shared metadata]: #song, #artist, #image-url  (#image-url shown only in system mode)
   [shared status]:   rec-led + state + timer
   [ START CAPTURE ]  (shared button)
```

**System-mode explainer text (verbatim intent):** "Capture anything playing on your Mac —
Apple Music, Spotify, SoundCloud, a file. You start and stop the capture yourself. **Start
the capture *before* the audio begins**, then stop it when the song ends."

**`app.js` changes (small):**
- `mode` state ∈ {`null`, `"youtube"`, `"system"`}.
- `selectMode(m)`: hide picker, show `#flow`, toggle `#yt-chrome`/`#sys-chrome`, the
  `#image-url` field, and the per-mode hint.
- `goBack()`: only when not recording — hide `#flow`, show `#mode-picker`.
- `begin()`: build the `/api/recordings/start` body per mode — `youtube_url` in YouTube
  mode, `image_url` (from `#image-url`) in system mode.
- `setState(recording)`: also disable the Back button while recording.

The YouTube embed/auto-stop logic (`onPlayerState` ENDED → auto-stop) is unchanged; it
only ever runs in YouTube mode because the embed exists only there.

## Icons

Custom **inline-SVG** glyphs, **grayscale**, visually reminiscent of but not copies of the
real marks (avoids trademark issues): Apple Music, Spotify, SoundCloud, and a generic
speaker for the system card's collage; a play-triangle for the YouTube card. Inline SVG
keeps the app self-contained (no external assets), consistent with the existing
`favicon.svg`. Implementation should use the `frontend-design` skill to make the cards and
collage match the existing analog aesthetic.

## Backend (small)

**`POST /api/recordings/start`** (`routes/api.py`): add optional `image_url: str | None`
to `StartRecordingIn`. After `recording.start(...)` returns the job id, if `image_url` is
set, fire `fetch_thumbnails.store_image_from_url_async(job_id, image_url)`. `recording.start`
is unchanged (the image is thumbnail-only, independent of the recorder).

**`fetch_thumbnails.py`** — new helpers alongside the existing ones:
- `_download_image(url, max_bytes=5*1024*1024) -> bytes | None`: like `_download`, but
  rejects non-`image/*` content types and caps size (read `max_bytes+1`, reject if over).
  Returns `None` on any error (never raises).
- `store_image_from_url(job_id, image_url)`: download via `_download_image`; on success
  write to `thumbnail_path(job_id)` (mkdir parents). Writes only the file — NOT the
  sidecar (the job is still `recording` at this point; `meta.json` gets written later by
  `process_capture` on success / `_fail` on failure, and `to_dict` includes the thumbnail
  when the file exists). On failure the tile just shows the placeholder.
- `store_image_from_url_async(job_id, image_url)`: daemon-thread wrapper.

No new imports needed in `routes/api.py` (it already imports `fetch_thumbnails`).

## Data flow

```
Landing → pick mode
  YouTube: paste link → embed plays → START CAPTURE (auto-stop on end) → /start {youtube_url}
           → process_capture fetches YouTube thumbnail (unchanged)
  System:  fill song/artist/image-url → START CAPTURE (manual stop) → /start {image_url}
           → store_image_from_url_async fetches + stores thumbnail.jpg (fetch-and-forget)
Both → stop → redirect to /jobs/{id} → stemming (unchanged)
```

## Edge cases

- **Back during recording:** Back is disabled once capture starts (must Stop first).
- **Image fetch fails / not an image / too large:** silently skipped; the library tile
  falls back to its placeholder (existing behavior via `thumbnail_ref` file-existence).
- **Both URLs somehow set:** independent paths; in practice system mode sends no
  `youtube_url` and YouTube mode sends no `image_url`, so no conflict.
- **SSRF note:** the URL is user-supplied in a local single-user app; `_download_image`
  still bounds it with a timeout, size cap, and content-type check.

## Testing

Automated (backend):
- `_download_image`: rejects non-image content type; rejects oversize; returns bytes on a
  valid image response (monkeypatch `urlopen`).
- `store_image_from_url`: writes `thumbnail_path(job_id)` on success (mkdir parents); is a
  no-op (no file, no raise) when download returns `None`.
- `POST /api/recordings/start` with `image_url` fires `store_image_from_url_async`
  (monkeypatch it to record the call, like the retry-endpoint test); without `image_url`
  it does not.

Manual (front-end):
- Mode picker renders two cards with the grayscale icons.
- YouTube card → existing flow works, Back returns to picker, Back disabled while
  recording.
- System card → song/artist/image-url + explainer + capture; a valid image URL shows as
  the library tile thumbnail after capture; Back disabled while recording.
