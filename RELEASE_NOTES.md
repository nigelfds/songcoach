# SongCoach v1.1.0

Four practice-workflow upgrades since v1.0.0 — capture a whole playlist hands-free, find recordings fast, tidy up your library, and carry it between Macs.

## ♫ Apple Music mode — auto-capture a playlist, song by song

A new mode that watches Apple Music and captures each song for you. Hit **Start Apple Music mode**, then just play a song or a playlist:

- SongCoach **automatically starts and stops** a capture at each song boundary and sends every finished song to the stem queue — song after song — until you click **Stop**.
- **Pause/resume follows the music** — pause in Apple Music and the recording pauses too (dead air isn't recorded); the song stays one clean take.
- Songs stem **one at a time** in the background (no more overloading your Mac when tracks are short), and the panel shows **live progress** — *queued → separating → done ✓* — so you can see everything finish after you stop.
- **Cover art** for each song is pulled from Apple Music onto its library tile.

First use asks macOS for permission to read Apple Music.

## 🔎 Search + pagination in the library

As your library grows, find things instantly:

- A **search box** filters by **song title and artist** as you type (matches all your words, in any order).
- The list is **paginated (10 per page)** with page navigation, so long libraries stay tidy.

Both are instant and run entirely in the app.

## 🗑 Delete a recording

Open a recording's player and use the new **trash button** (with a confirm) to remove it from your library. It's a **soft delete** — the item leaves your library, but the audio files on disk are never touched, so nothing is truly lost.

## ⤓⤒ Export & import your library

Move your recordings between Macs in two clicks:

- **Export** downloads your whole library as a single `.zip`.
- **Import** merges a `.zip` back in (anything with the same ID is overwritten; nothing you already have is removed).

Everything stays local — the `.zip` is the only thing that leaves your machine, and only if you move it yourself.

## Fixes & hardening

- Fixed a frozen-app crash where the Apple Music watcher could never see playback (an AppleScript reserved-word bug).
- Import is hardened against malformed/malicious archives (path-traversal and symlink members).
- Numerous reliability fixes around capture segmenting, the stem queue, and delete edge cases.

---

**macOS 13+ · Apple Silicon.** Signed & notarized — download `SongCoach.dmg` below, drag to Applications, and open. On first capture, grant **Screen & System Audio Recording** in *System Settings → Privacy & Security*.
