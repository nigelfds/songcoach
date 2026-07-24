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

## Phase 1 — freeze to an (unsigned) .app — DONE (2026-07-24)

**Done (code):**
- **Demucs runs in-process** — `separator.py` no longer shells out to
  `sys.executable -m demucs` (impossible when frozen). It drives Demucs' Python
  building blocks (`get_model` / `apply_model` / `save_audio`), mirroring
  `--two-stems drums --mp3 --mp3-bitrate 256` exactly, and caches the model.
- **Bundled-runtime setup** — `paths.setup_runtime()` (frozen only) prepends the
  resource dir to `PATH` (so subprocesses incl. Demucs' own ffmpeg call resolve
  the bundled binaries) and points `TORCH_HOME` at a writable Application Support
  dir seeded from the app's bundled weights (offline first run).
- **`SongCoach.spec`** + **`scripts/build_macapp.sh`** — the build.

**Data/DB:** not bundled at all. `ensure_dirs()` creates `data/` under
Application Support and `rebuild()` recreates an empty `songcoach.db` on first
launch — which *is* the "ship empty, rebuild on startup" requirement.

**You provide before building** (git-ignored `vendor/`):
- `vendor/ffmpeg`, `vendor/ffprobe` — **static arm64** builds (a dynamically
  linked system ffmpeg won't run in the bundle; use LGPL for distribution).
- `vendor/torch/hub/checkpoints/955717e8-8726e21a.th` — htdemucs weights (the
  build script copies it from `~/.cache/torch` if you've separated once).
- `native/syscap` — built by the script.

**Build:** `scripts/build_macapp.sh` → `dist/SongCoach.app`. Freezing torch/demucs
is fiddly — expect to add `hiddenimports` to the spec as PyInstaller reports
missing modules on your machine.

**Verified end-to-end (2026-07-24):** captured a YouTube video from the built
`.app`, separated it in-process, and played the stems back — the full
capture → Demucs → playback path works from the frozen bundle. (Also fixed a
frozen-only unpickle crash: `SongCoach.spec` now collects `numpy.core`/`numpy._core`
submodules.)

## Phase 2 — sign, notarize, DMG — runbook

Goal: a Developer ID–signed, notarized, stapled `.dmg` others can download and
double-click. Without notarization Gatekeeper blocks the app and the
screen-recording permission is unreliable.

### One-time prerequisites (your Apple account)

1. **Developer ID Application certificate** — Xcode → Settings → Accounts → your
   Apple ID → Manage Certificates → **+** → *Developer ID Application*. Confirm:
   ```
   security find-identity -v -p codesigning     # → "Developer ID Application: … (TEAMID)"
   ```
2. **Notary credentials** — create an app-specific password at appleid.apple.com,
   then store it once as a keychain profile:
   ```
   xcrun notarytool store-credentials songcoach-notary \
     --apple-id <you@example.com> --team-id <TEAMID> --password <app-specific-pw>
   ```
3. `brew install create-dmg`.

### Build → ship

```
scripts/release_macapp.sh          # build → sign → dmg → notarize + staple
```

Or run the stages individually:

| Script | Does |
|--------|------|
| `scripts/build_macapp.sh`    | PyInstaller → `dist/SongCoach.app` (unsigned) |
| `scripts/sign_macapp.sh`     | signs every nested mach-O inside-out, then the `.app` with `packaging/entitlements.plist` (hardened runtime) |
| `scripts/make_dmg.sh`        | `create-dmg` → `dist/SongCoach.dmg`, then signs the DMG |
| `scripts/notarize_macapp.sh` | `notarytool submit --wait` → `stapler staple` → `spctl` check |

Signing identity auto-detects the sole *Developer ID Application* cert, or set
`DEVID="Developer ID Application: … (TEAMID)"`. Notary profile defaults to
`songcoach-notary` (override with `NOTARY_PROFILE`).

### Entitlements (`packaging/entitlements.plist`)

Hardened runtime + `allow-jit`, `allow-unsigned-executable-memory`,
`disable-library-validation` — CPython/PyInstaller and torch's ~130 unsigned
`.dylib`/`.so` need these. If notarization fails, read the real reason:

```
xcrun notarytool log <submission-id> --keychain-profile songcoach-notary
```

The usual follow-up is adding `com.apple.security.cs.allow-dyld-environment-variables`,
then re-sign and resubmit.

### Acceptance test

Download `dist/SongCoach.dmg` on a **second Mac** (or a fresh user account),
double-click → drag to Applications → open. It must launch with no Gatekeeper
block, and **Screen & System Audio Recording** capture must work.

## Phase 3 — later

Auto-update (Sparkle), universal2 / Intel build, and a lighter CoreML/ONNX
separation engine to shrink the ~2 GB footprint.
