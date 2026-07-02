# Player UX backlog

From the 2026-07-02 usability review of the SongCoach player. Items 1–3 (per-stem
mixer, A–B loop readouts + shortcuts, consolidated global transport) are **done**.
The rest are captured here to pick up later.

## Done (2026-07-02)
- [x] **Per-stem mixer** — drums + no_drums play together, each with a volume fader
      and a single "in the mix" toggle (replaced the confusing mute+solo pair);
      `original` is a mutually-exclusive **REF** full-mix (avoids the double-count,
      since `original` = `drums` + `no_drums`).
- [x] **Clarity pass** — silent waveforms dim; the audible strips keep full color +
      channel glow; REF and the stems grey each other out since they're exclusive.
- [x] **A–B loop readouts + keyboard** — live `A / B / length`; `I`/`O` set in/out at
      playhead, `L` toggles loop, `Backspace`/`Delete` clears, `←/→` seek 5s,
      `Alt+←/→` nudge A, `Shift+←/→` nudge B (0.1s).
- [x] **Consolidated transport** — single global Play/Pause + Restart in a transport
      bar; per-strip buttons repurposed for mixing.

## Backlog

### 5. One shared timeline + playhead — DONE (2026-07-02)
- [x] Replaced the three per-strip `TimelinePlugin` rulers with a single shared
      timeline under the stack (hosted on the leader/FULL SONG WaveSurfer).
- [x] Native per-track cursors hidden (`cursorWidth: 0`); one overlay playhead line
      now spans all strips, mapped from time to the waveforms' x-range and relaid
      out on resize.
- Also fixed: LOOP now loops the whole song when no A–B section is set (toggle is
  always enabled; whole-song wrap handled on `finish`). CLEAR A–B only drops the
  section, leaving the LOOP state alone.

### 4. Finer / two-directional speed
Discrete 0.5/0.65/0.8/1.0 only. Drummers practice hard passages at 0.25–0.4×.
- Continuous rate control (~0.25×–1.25×) or finer presets.
- Surface that **pitch is preserved** (the flag is already set) so users trust slow-down.

### 5. One shared timeline + playhead
Three stacked `TimelinePlugin` rulers are redundant and eat vertical space.
- Single shared timeline (top or bottom of the stack) and one playhead line spanning
  all three strips.

### 6. Keyboard discoverability + polish
- A small "?" shortcuts legend / cheatsheet (Space, I/O/L, arrows, nudge modifiers).
- Confirm no double-fire when a control has focus (Space `preventDefault` guard is in;
  re-verify across browsers).

### 7. Focus-visible states
`.chip`, `.icon-btn`, `.strip__btn`, `.tbtn`, faders lack `:focus-visible` rings —
keyboard tabbing is invisible. Add accent-colored focus outlines across controls.

### 8. Header layout / hierarchy
Header still packs back / thumb / title / artist / edit. With the transport split out
it's better, but on small screens consider a cleaner two-tier title vs. transport split
and verify wrapping.

### 9. Honest "NOW PLAYING" label
Says NOW PLAYING before anything plays. Use TRACK / LOADED until playback starts,
then swap to NOW PLAYING.

## Quick wins / feature ideas
- [ ] Live time labels on the loop region edges while dragging.
- [ ] **Count-in / metronome click** toggle — most-requested drum-practice feature.
- [ ] Disable the transport until `onAllReady` so early clicks don't silently no-op.
