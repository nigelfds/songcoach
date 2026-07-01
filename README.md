# SongCoach 🥁

A practice tool for drum students. Paste a YouTube URL and SongCoach pulls the
audio, uses an AI source-separation model (Demucs) to split it into **the full
song**, **drums only**, and **the song without drums**, then gives you a
mobile-first player with waveforms, region selection, and looping so you can
drill any part of a song.

---

## Why

Drummers learn by playing along. Being able to isolate the drums (to hear
exactly what the drummer played) or mute the drums (to play along as if you're
the drummer), and to loop a tricky 4 bars over and over, is the core of
efficient practice. SongCoach automates the tedious part.

---

## Architecture

```
Browser (mobile-first web UI, WaveSurfer.js)
        │  submit YouTube URL
        ▼
FastAPI web dyno  ──enqueue──►  Redis  ──►  RQ worker dyno
        │                                        │
        │ poll job status                        │ 1. yt-dlp  → download audio (premium cookies)
        ▼                                        │ 2. Demucs  → drums.wav + no_drums.wav
   Player page  ◄──signed URLs───────────────────┤ 3. upload 3 stems
                                                 ▼
                                          S3 (or local disk in dev)
```

- **Web**: FastAPI + Jinja2 templates, served by Gunicorn/Uvicorn.
- **Worker**: RQ (Redis Queue) worker running the heavy download + separation
  pipeline off the request cycle (Demucs takes minutes and lots of RAM).
- **Storage**: pluggable — local filesystem in dev, **S3** in production.
- **DB**: SQLAlchemy over SQLite (dev) / Postgres (Heroku) for job + track state.
- **Frontend**: [WaveSurfer.js v7](https://wavesurfer.xyz/) for waveforms + the
  Regions plugin for select-and-loop.

### Key tools
- **yt-dlp** — YouTube audio extraction. Supports YouTube Premium via a cookies
  file (`YTDLP_COOKIES_FILE`) so downloads are ad-free / higher quality.
- **Demucs** (`htdemucs`, `--two-stems=drums`) — separates drums vs. the rest.
  Runs on CPU on Heroku (slow but works); GPU if available locally.
- **ffmpeg** — audio muxing/encoding (dependency of both above).

---

## Local development

### Docker Compose (recommended)

This is the primary local flow. It runs the app on **Linux with Postgres, Redis,
and the real forking RQ worker** — the same shape as production — so there are no
macOS-specific quirks (the native path needs a no-fork worker workaround).

Requires Docker Desktop.

```bash
cp .env.example .env          # defaults already work for Docker
docker compose up --build     # first build downloads torch (~a few minutes)
# open http://localhost:8000
```

Services that come up: `web` (port 8000, live-reload), `worker`, `db` (Postgres),
`redis`. Submit a URL on the landing page → `web` enqueues → `worker` downloads +
separates → the player polls until the stems are ready. Stems are shared between
containers via a volume and served at `/media`; Demucs model weights persist in a
named volume so they download only once.

> ✅ Verified end-to-end on this stack: YouTube download → Demucs separation →
> three stems (song / drums / no-drums) served over HTTP.

```bash
docker compose logs -f worker   # watch the pipeline
docker compose down             # stop (add -v to wipe db + stems + model cache)
```

### Native (pyenv) — fallback

```bash
brew install pyenv ffmpeg redis
pyenv install 3.11.9          # Demucs/PyTorch need 3.11, not 3.14
pyenv local 3.11.9
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# three terminals:
redis-server
python worker.py                      # SimpleWorker on macOS (avoids a fork crash)
uvicorn songcoach.main:app --reload   # → http://localhost:8000
```

> No-Redis smoke test: set `RUN_JOBS_INLINE=true` and leave `REDIS_URL` unset —
> jobs then run in a background thread inside the web process.

### YouTube Premium / avoiding ads (cookies)

Without cookies, downloads work for many videos but you may hit ads, age-gates,
or "Sign in to confirm you're not a bot". Supplying your logged-in **Premium**
cookies fixes that and gives higher-quality audio.

**A) `cookies.txt` file — works for Docker *and* native (recommended)**
1. Install a Netscape-format cookie exporter, e.g. the
   [**Get cookies.txt LOCALLY**](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   extension.
2. Log into YouTube (Premium account), open `https://www.youtube.com`, click the
   extension, and **Export** → save as `cookies.txt` in the **project root**.
3. `.env` already points at it (`YTDLP_COOKIES_FILE=./cookies.txt`). The Docker
   bind-mount makes it available inside the containers automatically; if the file
   is absent, downloads just proceed without cookies.

`cookies.txt` is git-ignored. Cookies expire periodically — re-export if
downloads start failing with auth errors.

**B) Read straight from your browser — native (pyenv) only**
Not available under Docker (no browser in the container). Set in `.env`:
```
YTDLP_COOKIES_FROM_BROWSER=chrome     # or: safari | firefox | edge | brave
```
Be signed into YouTube in that browser. macOS notes:
- **Chrome/Brave/Edge**: fully quit the browser first (it locks its cookie DB).
  macOS may prompt for Keychain access — click **Always Allow**.
- **Safari**: grant your terminal **Full Disk Access** (System Settings → Privacy
  & Security) so it can read the cookie store.
- **Firefox**: works without quitting.

**On Heroku** (no browser/file available): paste the exported cookies.txt
*contents* into the `YTDLP_COOKIES` config var; the worker materialises a temp
file at runtime. (`heroku config:set YTDLP_COOKIES="$(cat cookies.txt)"`.)

> Verify quickly from the CLI:
> `.venv/bin/yt-dlp --cookies-from-browser chrome -F "https://youtu.be/<id>"`

---

## Deployment (Heroku)

```bash
heroku create
heroku stack:set heroku-24
heroku addons:create heroku-postgresql:essential-0
heroku addons:create heroku-redis:mini
heroku buildpacks:add heroku/python
heroku buildpacks:add https://github.com/heroku/heroku-buildpack-apt   # installs ffmpeg
# set config vars: AWS_*, S3_BUCKET, YTDLP_COOKIES, SECRET_KEY ...
git push heroku main
heroku ps:scale web=1 worker=1     # worker dyno needs enough RAM for Demucs
```

`Aptfile` installs `ffmpeg`. `Procfile` defines the `web` and `worker` dynos.

> ⚠️ Demucs is memory-hungry. Use a `standard-2x` (or larger) dyno for the
> worker, or run the mdx_extra_q lighter model. See Phase 7 notes.

---

## Roadmap / progress tracker

Legend: ✅ done · 🚧 in progress · ⬜ not started

### Phase 0 — Tooling & scaffolding
- ✅ Install system deps via Homebrew (pyenv, ffmpeg, redis)
- ✅ pyenv + Python 3.11 virtualenv
- ✅ Project skeleton, requirements, Procfile, config, `.env.example`
- ✅ Docker Compose dev stack (Linux/Postgres/Redis) — prod-parity local env

### Phase 1 — Backend skeleton
- ✅ FastAPI app + settings/config loader
- ✅ SQLAlchemy models: `Job`, `Track`
- ✅ Storage abstraction (local disk ↔ S3)
- ✅ Health check + basic error handling

### Phase 2 — YouTube download
- ✅ yt-dlp wrapper: URL → audio file
- ✅ Premium cookies support (`YTDLP_COOKIES_FILE` / `YTDLP_COOKIES`)
- ✅ URL validation + metadata (title, duration, thumbnail)

### Phase 3 — Source separation
- ✅ Demucs wrapper: audio → `drums` + `no_drums` (`--two-stems`)
- ✅ Produce the 3 deliverable tracks (original / drums / no-drums)
- ✅ Stems written straight to mp3 (256k) for web playback

### Phase 4 — Background jobs
- ✅ RQ + Redis queue wiring (`worker.py`)
- ✅ Full pipeline task (download → separate → upload → mark done)
- ✅ Inline fallback for local dev (`RUN_JOBS_INLINE`)
- ✅ Progress/status states + polling endpoint

### Phase 5 — Submit & status UI
- ✅ Mobile-first landing page with URL form
- ✅ Processing/status screen with live progress meter

### Phase 6 — Player UI
- ✅ Three synced WaveSurfer waveforms (song / drums / no-drums)
- ✅ Play/pause/seek, solo (listen) track switching
- ✅ Region select + A–B loop (drag on any waveform)
- ✅ Responsive layout: phone → iPad → desktop
- ✅ Pitch-preserved slow-down (0.5×–1×) for practice

### Phase 7 — Storage & deploy
- ✅ S3 upload + signed URLs (`S3Storage`)
- ✅ Aptfile/Procfile/app.json for Heroku
- ✅ Postgres + Redis addons, config vars documented
- ⬜ First successful deploy (needs a Heroku account + AWS creds)

### Phase 8 — Polish (backlog)
- ⬜ Job list / history, delete/cleanup
- ⬜ Optional user accounts / auth
- ⬜ Metronome / count-in overlay
- ⬜ Tests (pipeline, storage, API)
- ⬜ Rate limiting & cost guards on separation

---

## Project layout

```
songcoach/
├── songcoach/
│   ├── main.py            # FastAPI app factory + routes wiring
│   ├── config.py          # env-driven settings
│   ├── db.py              # SQLAlchemy engine/session
│   ├── models.py          # Job, Track
│   ├── storage.py         # local/S3 storage backend
│   ├── jobs.py            # queue + inline dispatch
│   ├── pipeline/
│   │   ├── downloader.py  # yt-dlp
│   │   └── separator.py   # Demucs
│   ├── routes/            # pages + JSON API
│   ├── templates/         # Jinja2 (base, index, player)
│   └── static/            # css + js (WaveSurfer player)
├── worker.py              # RQ worker entrypoint
├── Dockerfile  docker-compose.yml  .dockerignore   # local dev stack
├── Procfile  Aptfile  runtime.txt  requirements.txt  app.json   # Heroku
└── README.md
```
```
```
