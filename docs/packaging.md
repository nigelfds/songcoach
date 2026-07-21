# Packaging SongCoach as a macOS app

Goal: distribute to other Mac users as a **double-clickable, signed & notarized
`.app`** in a `.dmg`. Decisions (2026-07-21):

- **Freeze** with **PyInstaller**; wrap the local FastAPI server in a **pywebview**
  (WKWebView) window so the "Screen & System Audio Recording" TCC permission
  attaches to *SongCoach*, not Terminal.
- **Bundle Demucs/PyTorch as-is** (~2–2.5 GB app) — works offline, no engine change.
- **arm64 (Apple Silicon) only** for v1.

## Cache is rebuilt from disk on startup (ship an empty scaffold)

The stem folders + `meta.json` sidecars under `data/jobs/<id>/` are the source of
truth; `songcoach.db` is a disposable index. So:

- **The bundle ships _no_ user recordings and _no_ populated DB.** Include an empty
  `data/` scaffold (and optionally an empty `songcoach.db`) only.
- **On every launch the app rebuilds the cache** from whatever is on disk
  (`main.py` → `paths.ensure_dirs()` + `rebuild()`), so a fresh install starts empty
  and a returning user reindexes their `data/`. Schema changes are a free
  drop-and-recreate.
- In the packaged app, user data lives in `~/Library/Application Support/SongCoach/`
  (writable); the `.app` itself is read-only.

## Phase 0 — bundle-ready code — DONE (2026-07-21)

- `songcoach/paths.py` — resolves data dir, DB, resource dir, and helper binaries
  for **dev** (repo / `./data` / `./songcoach.db` / PATH) vs **frozen**
  (`sys._MEIPASS` / Application Support). Dev behaviour is unchanged.
- `config.py` — `database_url`, `local_storage_dir`, `ffmpeg_bin`, `ffprobe_bin`
  derive from `paths` via `default_factory` (env vars still override).
- `pipeline/recorder.py`, `pipeline/process.py` — use `settings.ffprobe_bin` /
  `settings.ffmpeg_bin` and resolve `syscap` against `paths.resource_dir()`
  (GUI-launched apps have a minimal PATH, so bare `ffmpeg`/`ffprobe` won't resolve).
- `main.py` — rebuilds the cache from `data/` on startup (see above).
- `songcoach/desktop.py` — launcher: free port → uvicorn thread → wait for
  `/healthz` → pywebview window. Entry point for the bundle. `webview` is imported
  lazily so the module still imports in dev without pywebview.
- `requirements.txt` — added `pywebview`, `pyinstaller`.

Verified: dev paths resolve exactly as before; startup rebuild reindexes the real
`data/` into a throwaway DB (real `songcoach.db` untouched).

> Dev note: with rebuild-on-startup, **don't run two servers against the same
> `./songcoach.db`** — each launch drops + recreates it. Stop the `uvicorn` dev
> server before testing `python -m songcoach.desktop`.

## Phase 1 — freeze to an (unsigned) .app — TODO

Blockers/must-dos before this works from a bundle:

1. **Demucs must run in-process.** `separator.py` currently shells out via
   `sys.executable -m demucs`, which is impossible in a frozen app. Switch to the
   Demucs Python API (`demucs.api.Separator`) so torch runs in-process.
2. **Bundle helper binaries:** `native/syscap` (→ `native/syscap`), plus
   `ffmpeg` + `ffprobe` (LGPL build; include license notice).
3. **Bundle model weights:** ship `htdemucs` weights and point torch's hub cache at
   them (e.g. `TORCH_HOME`) so first run works offline.
4. PyInstaller spec: entry `songcoach/desktop.py`; `datas` for `templates/`,
   `static/`, `native/syscap`, `ffmpeg`, `ffprobe`, model weights; a minimal empty
   `data/` scaffold. Collect torch/demucs data files (they need runtime data).
5. Validate capture → separation → playback entirely from the `.app`.

## Phase 2 — sign, notarize, DMG — TODO (needs your Apple Developer cert)

- Developer ID Application signing of the `.app` **and every nested binary**
  (`syscap`, `ffmpeg`, `ffprobe`, torch's many `.dylib`s/`.so`s) with the
  **hardened runtime**.
- Entitlements + `Info.plist` usage strings as required for ScreenCaptureKit.
- `notarytool submit --wait` → `stapler staple`.
- Package a `.dmg` (e.g. `create-dmg`).
- Without notarization, Gatekeeper blocks the app and the screen-recording
  permission is unreliable.

## Phase 3 — later

Auto-update (Sparkle), universal2 / Intel build, and a lighter CoreML/ONNX
separation engine to shrink the ~2 GB footprint.
