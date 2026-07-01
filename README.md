# SongCoach 🥁

A local practice tool for drum students, for macOS. Give a recording a name,
tap **capture**, and play the song through your Mac (a YouTube tab, Spotify, a
file — anything). SongCoach records the system audio, uses an AI
source-separation model (Demucs) to split it into **the full song**, **drums
only**, and **the song without drums**, then gives you a player with three
synced waveforms, region selection, and looping so you can drill any part of a
song.

It runs entirely on your machine — no accounts, no cloud, no downloads.

---

## Why

Drummers learn by playing along. Being able to isolate the drums (to hear
exactly what the drummer played) or mute the drums (to play along as if you're
the drummer), and to loop a tricky 4 bars over and over, is the core of
efficient practice. SongCoach automates the tedious part.

Capturing **system audio** — rather than downloading from a URL — means it works
with whatever you can already play: a logged-in YouTube/Premium tab, a streaming
service, a local file. No format breakage, no auth dance.

---

## Architecture

```
Browser (WaveSurfer.js UI)
        │  1. enter metadata, tap Start        ┌─────────────────────────┐
        ▼                                       │  native/syscap (Swift)  │
FastAPI app  ──start/stop capture──►  Recorder ─┤  ScreenCaptureKit tap   │
        │                                       │  → capture.m4a          │
        │ poll job status                       └─────────────────────────┘
        ▼                                              │ on Stop
   Player page  ◄── /media stem URLs ──┐               ▼
                                       │   background thread: Demucs
                                       └── SQLite + local filesystem (./data)
                                              → drums.mp3 · no_drums.mp3 · original.mp3
```

- **Capture**: `native/syscap.swift` — a small Swift **ScreenCaptureKit** helper
  compiled to `native/syscap`. It taps the Mac's system-audio output to an
  `.m4a`. No BlackHole / virtual audio device needed. Python drives it as a
  subprocess (`pipeline/recorder.py`).
- **Web**: FastAPI + Jinja2 templates, served by Uvicorn. Single-user, so
  separation runs **inline in a daemon thread** (no queue) and the UI polls job
  status.
- **Separation**: **Demucs** (`htdemucs`, `--two-stems=drums --mp3`) splits drums
  vs. the rest, writing 256 kbps mp3 directly.
- **Storage**: local filesystem under `./data`, served at `/media/{key}`.
- **DB**: SQLAlchemy over **SQLite** (`songcoach.db`, WAL mode) for job + track
  state.
- **Frontend**: [WaveSurfer.js v7](https://wavesurfer.xyz/) for waveforms + the
  Regions plugin for select-and-loop.
- **ffmpeg** — audio muxing/encoding (Demucs dependency; also used to build the
  full-song mp3).

---

## Requirements

- **macOS** (system-audio capture uses ScreenCaptureKit; macOS 13+).
- **Xcode command-line tools** (for `swiftc`) to build the capture helper.
- **Python 3.11** — Demucs/PyTorch don't support 3.14. (`.python-version` pins
  3.11.9.)
- **ffmpeg**.

---

## Setup & running

```bash
brew install pyenv ffmpeg
pyenv install 3.11.9          # Demucs/PyTorch need 3.11, not 3.14
pyenv local 3.11.9
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build the native capture helper (the compiled binary is git-ignored):
swiftc -O native/syscap.swift -o native/syscap

# Run:
uvicorn songcoach.main:app --reload   # → http://localhost:8000
```

### Granting the capture permission

The first capture will fail until macOS is granted **Screen & System Audio
Recording** permission — it gates system-audio capture:

1. Open **System Settings → Privacy & Security → Screen & System Audio
   Recording**.
2. Enable the app hosting the process (your **terminal** — Terminal / iTerm /
   VS Code — since it's the "responsible process" for `syscap` in dev).
3. **Fully quit and reopen** that app; TCC changes only take effect after a
   restart.

If you see `The user declined TCCs for application, window, display capture`,
that's this permission. (When SongCoach is later packaged as a `.app`, the
permission attaches to the app itself.)

### Using it

1. On the home page, enter a **song name** (artist and a reference YouTube URL
   are optional).
2. Start playing the song through your Mac, then tap **Start Capture**.
3. Tap **Stop & Separate** when done. Demucs runs (a minute or two); the page
   shows progress, then loads the player.
4. The home page lists every recording and its stem files; click one to reopen
   its player.

In the player: three synced waveform rows (full / drums / no-drums), **solo** to
choose the audible track, **drag on any waveform** to set an A–B loop, and a
**0.5×–1×** pitch-preserved slow-down.

---

## Configuration

Settings are env-driven (`.env`, via pydantic-settings). Defaults work out of the
box; notable knobs:

- `DATABASE_URL` — default `sqlite:///./songcoach.db`.
- `LOCAL_STORAGE_DIR` — where recordings/stems live (default `./data`).
- `SYSCAP_BIN` — path to the compiled helper (default `native/syscap`).
- `DEMUCS_MODEL` — default `htdemucs`.
- `MAX_DURATION_SECONDS` — safety cap on capture length (default 600).

---

## Roadmap / progress tracker

Legend: ✅ done · 🚧 in progress · ⬜ not started

### Core pipeline
- ✅ Native ScreenCaptureKit helper (`native/syscap.swift`) → system-audio `.m4a`
- ✅ Python `Recorder` (start/stop) driving syscap as a subprocess
- ✅ Demucs wrapper: audio → `drums` + `no_drums` (`--two-stems`, mp3 256k)
- ✅ Inline (daemon-thread) separation + progress/status polling
- ✅ SQLite + local-filesystem storage (served at `/media`)

### UI
- ✅ Home page: recording metadata (song / artist / optional URL) + Start/Stop capture
- ✅ Home page: library list of recordings with their available stem files
- ✅ Processing screen with live progress meter
- ✅ Player: three synced WaveSurfer waveforms with solo switching
- ✅ Region select + A–B loop (drag on any waveform)
- ✅ Pitch-preserved slow-down (0.5×–1×)
- ✅ Responsive layout: phone → iPad → desktop

### Backlog
- ⬜ Package as a native `.app` (pywebview shell) so the permission attaches to SongCoach itself
- ⬜ Embedded browser for logging into sources inside the app
- ⬜ Level meter / auto-detect "is audio playing"
- ⬜ Delete/cleanup recordings from the library
- ⬜ Metronome / count-in overlay
- ⬜ Tests (pipeline, recorder, API)

---

## Project layout

```
songcoach/
├── native/
│   └── syscap.swift        # ScreenCaptureKit system-audio helper (build → native/syscap)
├── songcoach/
│   ├── main.py             # FastAPI app factory + routes wiring
│   ├── config.py           # env-driven settings
│   ├── db.py               # SQLAlchemy engine/session (SQLite)
│   ├── models.py           # Job, Track
│   ├── storage.py          # local-filesystem storage
│   ├── jobs.py             # background-thread dispatch
│   ├── recording.py        # active-capture session manager
│   ├── pipeline/
│   │   ├── recorder.py     # drives native/syscap
│   │   ├── process.py      # separate → publish stems → mark done
│   │   └── separator.py    # Demucs
│   ├── routes/             # pages + JSON API
│   ├── templates/          # Jinja2 (base, index, player)
│   └── static/             # css + js (WaveSurfer player)
├── requirements.txt
└── README.md
```
