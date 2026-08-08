# SongCoach v1.2.0

Hear the singer, isolate the band, and mark up your practice — plus a way to
bring your whole back catalogue up to speed.

## 🎤 A vocals stem — now four stems per song

Every song now separates into **four** rows instead of three:

- **FULL SONG** (reference mix),
- **DRUMS** — the kit, solo,
- **VOCALS** — the voice, solo, and
- **BACKING** — bass, keys & the rest, with drums *and* vocals removed.

Blend them with the per-stem faders. To play along, just drop **DRUMS** out of
the mix and you've got the whole song minus the kit — vocals and all.

## ♻️ Reprocess — upgrade older recordings to four stems

Recordings you made before the vocals stem existed still show three rows. Bring
them up to date without re-recording:

- **Per song** — open its player and hit the **reprocess** button (the
  circular-arrows icon, next to edit and delete). It re-separates the song and
  adds the vocals + backing stems. Your **markers are kept**.
- **Whole library** — run `python -m songcoach.reprocess` from the source tree to
  upgrade every recording at once, one at a time (`--force` to redo ones that
  already have vocals).

Reprocessing works from the full-song audio each recording already keeps, so
nothing needs re-capturing.

## 📍 Waveform markers

Annotate a recording so you can find the good bits fast:

- Click the **flag** icon in the player, then click a waveform to drop a marker —
  a line spanning **all** the stems with a small **"i"** badge.
- Give each one a **name** ("guitar solo", "big fill", "key change"). Placing one
  shows the exact timestamp, and while placing, a live time tooltip follows your
  cursor so you can land it precisely.
- Click a marker's **"i"** to rename or delete it. Markers are saved with the
  recording and survive edits and reprocessing.

## Polish

- The four stem rows now sit on the clean light panel, with each stem's fader and
  toggle tinted its channel colour.
- Apple Music mode's session panel gained a **refresh** control so finished songs
  show up in the library without relaunching.

---

**macOS 13+ · Apple Silicon.** Signed & notarized — download `SongCoach.dmg`
below, drag to Applications, and open. On first capture, grant **Screen & System
Audio Recording** in *System Settings → Privacy & Security*.
