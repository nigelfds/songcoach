import WaveSurfer from "https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.esm.js";
import RegionsPlugin from "https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.esm.js";
import TimelinePlugin from "https://unpkg.com/wavesurfer.js@7/dist/plugins/timeline.esm.js";

const app = document.getElementById("app");
const jobId = app.dataset.jobId;

// color = played (progress), dim = unplayed waveform, tuned for the light UI.
// NOTE: `original` == `drums` + `no_drums` (Demucs two-stem output), so the two
// stems are the real mixer; `original` is a mutually-exclusive REF full mix.
const KINDS = [
  { kind: "original", name: "FULL SONG", sub: "reference mix", color: "#6d45e6", dim: "#c9bcf5" },
  { kind: "drums",    name: "DRUMS",     sub: "the kit, solo", color: "#e8760a", dim: "#f6cf9f" },
  { kind: "no_drums", name: "NO DRUMS",  sub: "play along",    color: "#0e9e90", dim: "#a6ded7" },
];

const REGION_COLOR = "rgba(232,118,10,.16)";
const EPS = 0.03;         // "close enough to the boundary" slop
const DRIFT = 0.12;       // resync a stem only if it drifts past this many seconds
const NUDGE = 0.1;        // keyboard boundary nudge, seconds
const SEEK = 5;           // keyboard arrow seek, seconds

// Timeline density. NOTE: primary/secondaryLabelInterval are in SECONDS. Fine
// notches on short clips; on long songs the labels are spaced out (dark 30s /
// light 15s) so they don't crowd, and the dark primary labels also drop to a
// second row below the ruler — see the #timeline CSS.
function timelineOptions(dur) {
  if (dur <= 20) return { timeInterval: 0.1, primaryLabelInterval: 10, secondaryLabelInterval: 5 };
  if (dur <= 120) return { timeInterval: 1, primaryLabelInterval: 10, secondaryLabelInterval: 5 };
  return { timeInterval: 5, primaryLabelInterval: 30, secondaryLabelInterval: 15 };
}

const STAGE_LABEL = {
  recording: "RECORDING", queued: "QUEUED", separating: "SEPARATING",
  uploading: "UPLOADING", done: "READY", failed: "FAILED",
};

// ---------------------------------------------------------------------------
// 1. Poll job status until ready
// ---------------------------------------------------------------------------
const processingEl = document.getElementById("processing");
const playerEl = document.getElementById("player");

async function poll() {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    const job = await res.json();
    updateProcessing(job);

    if (job.status === "done") {
      initPlayer(job);
      return;
    }
    if (job.status === "failed") {
      showError(job);
      return;
    }
  } catch (e) {
    // transient network error; keep polling
  }
  setTimeout(poll, 2500);
}

function updateProcessing(job) {
  document.getElementById("stage-tag").textContent = STAGE_LABEL[job.status] || job.status;
  document.getElementById("meter-fill").style.width = Math.max(6, job.progress) + "%";
  if (job.title) {
    document.getElementById("processing-title").textContent = job.title;
  }
}

function showError(job) {
  const el = document.getElementById("processing-error");
  el.textContent = job.error || "Processing failed.";
  el.hidden = false;
  document.getElementById("stage-tag").textContent = "FAILED";
  document.getElementById("processing-hint").hidden = true;
  document.getElementById("meter-fill").style.background = "var(--red)";
  document.getElementById("retry-btn").hidden = !job.resumable;
}

document.getElementById("retry-btn").addEventListener("click", async () => {
  const retry = document.getElementById("retry-btn");
  retry.disabled = true;
  try {
    const res = await fetch(`/api/jobs/${jobId}/retry`, { method: "POST" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Couldn't retry.");
    }
    // Reset the processing UI and resume polling from queued.
    document.getElementById("processing-error").hidden = true;
    document.getElementById("processing-hint").hidden = false;
    document.getElementById("meter-fill").style.background = "";
    retry.hidden = true;
    retry.disabled = false;
    poll();
  } catch (err) {
    document.getElementById("processing-error").textContent = err.message;
    retry.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// 2. Build the player
// ---------------------------------------------------------------------------
// One shared playhead: all stems play together and are kept time-aligned.
// Gain routing decides what you actually hear (see applyGains).
const channels = [];      // { kind, ws, regions, el, isRef, volume, muted, solo }
let currentJob = null;    // latest job metadata (for the editor)
let leaderIndex = 0;      // the timekeeper stem that drives the readout + boundary
let refOn = false;        // listening to the untouched FULL SONG reference
let ready = 0;
// A–B section selected on the waveforms: null bounds = whole track.
let loop = { enabled: false, start: null, end: null };
let syncing = false;      // guard while we programmatically move the playheads / regions

// All stems mix through ONE Web Audio graph. WebKit/WKWebView won't play several
// <audio> elements at once and ignores their `.volume`, so each stem's element is
// tapped into this context and its level set with a GainNode instead.
const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

const curEl = document.getElementById("cur");
const durEl = document.getElementById("dur");
const playBtn = document.getElementById("play");
const restartBtn = document.getElementById("restart");
const loopToggle = document.getElementById("loop-toggle");
const sectionEl = document.getElementById("section");
const playheadEl = document.getElementById("playhead");
const nowStatus = document.getElementById("now-status");
let phGeom = null;        // cached geometry mapping time -> the shared playhead's x
let markers = [];               // [{id, time, name}]
let editingMarkerId = null;
let editingIsNew = false;
let markerMode = false;

function leader() { return channels[leaderIndex]; }
function duration() { return leader() ? leader().ws.getDuration() : 0; }
function clamp(t) { return Math.min(Math.max(0, t), duration() || 0); }

function setThumb(url) {
  const thumb = document.getElementById("console-thumb");
  if (url) {
    thumb.src = url;
    thumb.hidden = false;
  } else {
    thumb.removeAttribute("src");
    thumb.hidden = true;
  }
}

function initPlayer(job) {
  currentJob = job;
  document.getElementById("track-title").textContent = job.title || "Untitled";
  document.getElementById("track-artist").textContent = job.artist || "";
  setThumb(job.thumbnail_url);
  const byKind = Object.fromEntries(job.tracks.map((t) => [t.kind, t]));
  const stripsEl = document.getElementById("strips");

  KINDS.forEach((meta) => {
    const track = byKind[meta.kind];
    if (!track) return;

    const isRef = meta.kind === "original";
    const ctrls = isRef
      ? `<button class="strip__btn strip__ref" type="button" aria-pressed="false"
                 title="Listen to the untouched full mix">REF</button>`
      : `<span class="strip__vol-ico" aria-hidden="true" title="Volume">
           <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
             <path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 8.5a4 4 0 0 1 0 7"/>
           </svg>
         </span>
         <input class="strip__vol" type="range" min="0" max="100" value="100"
                title="Volume" aria-label="${meta.name} volume" />
         <button class="strip__btn strip__active" type="button" role="switch" aria-pressed="true"
                 title="In the mix" aria-label="${meta.name} in the mix">✓</button>`;

    const strip = document.createElement("section");
    strip.className = "strip";
    strip.dataset.kind = meta.kind;
    strip.innerHTML = `
      <div class="strip__top">
        <div class="strip__id">
          <span class="strip__dot"></span>
          <div>
            <div class="strip__name">${meta.name}</div>
            <div class="strip__sub">${meta.sub}</div>
          </div>
        </div>
        <div class="strip__ctrls">${ctrls}</div>
      </div>
      <div class="wave"><div class="wave__loading">DECODING…</div></div>`;
    stripsEl.appendChild(strip);

    const waveEl = strip.querySelector(".wave");
    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: waveEl,
      height: 72,
      waveColor: meta.dim,
      progressColor: meta.color,
      cursorWidth: 0,      // one shared overlay playhead spans all strips instead
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      dragToSeek: false,   // dragging selects a section; click seeks
      url: track.url,
      plugins: [regions],
    });

    const c = { kind: meta.kind, ws, regions, el: strip, isRef, volume: 1, active: true };

    ws.on("decode", () => {
      const loading = waveEl.querySelector(".wave__loading");
      if (loading) loading.remove();
      // Route this stem through the shared AudioContext: its <audio> output feeds
      // a GainNode we control (WebKit ignores media.volume and won't play multiple
      // <audio> elements at once — the graph mixes them into one output instead).
      if (!c.gain) {
        try {
          const source = audioCtx.createMediaElementSource(ws.getMediaElement());
          c.gain = audioCtx.createGain();
          c.gain.gain.value = 0;      // applyGains() sets the real level once ready
          source.connect(c.gain).connect(audioCtx.destination);
        } catch (e) {
          console.warn("Web Audio routing failed for", meta.kind, e);
        }
      }
      // the leader (FULL SONG) hosts the deck's single shared timeline; register it
      // now that the duration is known so tick density fits the track length.
      if (isRef) {
        ws.registerPlugin(TimelinePlugin.create({
          container: document.getElementById("timeline"),
          height: 22,
          style: { fontSize: "10px", color: "#57534a" },
          ...timelineOptions(ws.getDuration()),
        }));
      }
      regions.enableDragSelection({ color: REGION_COLOR });
      if (++ready === channels.length) onAllReady();
    });

    // drag-select on ANY strip drives one shared A–B section
    regions.on("region-created", (r) => applySectionFromRegion(r));
    regions.on("region-updated", (r) => applySectionFromRegion(r));

    // click anywhere on a waveform to seek all stems together
    ws.on("interaction", (newTime) => seekAll(newTime));

    ws.on("timeupdate", (t) => {
      if (c !== leader() || syncing) return;
      driftCorrect(t);
      handleBoundary(t);
      curEl.textContent = fmt(t);
      updatePlayhead(t);
    });

    ws.on("finish", () => {
      if (c !== leader()) return;
      // whole-song loop: no A–B section but LOOP is on → restart from the top
      if (loop.enabled && loop.start == null) {
        seekAll(0);
        channels.forEach((c) => c.ws.play());
      }
      updateStrips();
    });

    if (isRef) {
      strip.querySelector(".strip__ref").addEventListener("click", (ev) => {
        toggleRef();
        ev.currentTarget.blur();
      });
    } else {
      strip.querySelector(".strip__vol").addEventListener("input", (ev) => {
        c.volume = ev.target.value / 100;
        if (c.volume > 0) c.active = true;   // pushing a fader up brings the stem in
        refOn = false;                        // leave REF mode; you're mixing stems
        applyGains();
      });
      strip.querySelector(".strip__active").addEventListener("click", (ev) => {
        c.active = !c.active;
        refOn = false;                        // toggling a stem exits REF mode
        applyGains();
        ev.currentTarget.blur();
      });
    }

    channels.push(c);
  });

  leaderIndex = Math.max(0, channels.findIndex((c) => c.kind === "original"));
}

function onAllReady() {
  processingEl.hidden = true;
  playerEl.hidden = false;
  durEl.textContent = fmt(duration());
  channels.forEach((c) => c.ws.setPlaybackRate(currentRate(), true));
  applyGains();     // sets default volumes + updates strips
  layoutPlayhead();
  loadMarkers();
}

// ---------------------------------------------------------------------------
// 3. Gain routing — drums + no_drums are the mixer; original is the REF full mix
// ---------------------------------------------------------------------------
function gainFor(c) {
  if (refOn) return c.isRef ? 1 : 0;   // reference: only the untouched full mix
  if (c.isRef) return 0;               // mixing stems: REF stays silent
  return c.active ? c.volume : 0;
}

function applyGains() {
  channels.forEach((c) => { if (c.gain) c.gain.gain.value = gainFor(c); });
  updateStrips();
}

function toggleRef() {
  refOn = !refOn;
  applyGains();
}

// ---------------------------------------------------------------------------
// 4. Transport — global play/pause + restart, all stems synced
// ---------------------------------------------------------------------------
function isPlaying() { return !!leader() && leader().ws.isPlaying(); }

function playAll() {
  if (audioCtx.state === "suspended") audioCtx.resume();   // unlock on the user gesture
  // If a section is set and we're outside it, start from A.
  if (loop.start != null) {
    const t = leader().ws.getCurrentTime();
    if (t < loop.start - 0.01 || t >= loop.end - EPS) seekAll(loop.start);
  }
  channels.forEach((c) => c.ws.play());
  updateStrips();
}

function pauseAll() {
  channels.forEach((c) => c.ws.pause());
  updateStrips();
}

function togglePlay() { isPlaying() ? pauseAll() : playAll(); }

function restart() { seekAll(loop.start != null ? loop.start : 0); }

function seekAll(t) {
  syncing = true;
  channels.forEach((c) => c.ws.setTime(t));
  syncing = false;
  curEl.textContent = fmt(t);
  updatePlayhead(t);
}

// The shared playhead is a single overlay line spanning every strip, so vertical
// alignment across the stems is obvious. It maps time -> the waveforms' x-range.
function layoutPlayhead() {
  if (!channels.length) return;
  const base = document.getElementById("strips").getBoundingClientRect();
  const first = channels[0].el.querySelector(".wave").getBoundingClientRect();
  const last = channels[channels.length - 1].el.querySelector(".wave").getBoundingClientRect();
  phGeom = {
    left: first.left - base.left,
    width: first.width,
    top: first.top - base.top,
    height: last.bottom - first.top,
  };
  playheadEl.style.top = phGeom.top + "px";
  playheadEl.style.height = phGeom.height + "px";
  playheadEl.hidden = false;
  updatePlayhead(leader().ws.getCurrentTime());
}

function updatePlayhead(t) {
  if (!phGeom) return;
  const dur = duration() || 1;
  const x = phGeom.left + (Math.min(Math.max(0, t), dur) / dur) * phGeom.width;
  playheadEl.style.left = x + "px";
}

window.addEventListener("resize", () => { if (phGeom) { layoutPlayhead(); layoutMarkers(); } });

// Keep the non-leader stems locked to the leader, but only correct real drift
// so we don't reset currentTime (and glitch audio) on every frame.
function driftCorrect(t) {
  syncing = true;
  channels.forEach((c, idx) => {
    if (idx === leaderIndex) return;
    if (Math.abs(c.ws.getCurrentTime() - t) > DRIFT) c.ws.setTime(t);
  });
  syncing = false;
}

// At the section end: loop back if looping, otherwise stop and park at the start.
function handleBoundary(t) {
  if (loop.start == null || t < loop.end - EPS) return;
  if (loop.enabled) {
    seekAll(loop.start);
  } else {
    pauseAll();
    seekAll(loop.start);
  }
}

function updateStrips() {
  const anyStemActive = channels.some((c) => !c.isRef && c.active);
  channels.forEach((c) => {
    // audible => full-color waveform + channel glow; silent => dimmed.
    c.el.dataset.audible = String((c.gain ? c.gain.gain.value : 0) > 0.0001);
    if (c.isRef) {
      // REF is irrelevant while you're mixing stems, and vice-versa.
      c.el.dataset.dimmed = String(anyStemActive && !refOn);
      c.el.querySelector(".strip__ref")?.setAttribute("aria-pressed", String(refOn));
    } else {
      c.el.dataset.dimmed = String(refOn);
      c.el.querySelector(".strip__active")?.setAttribute("aria-pressed", String(c.active));
    }
  });
  const playing = isPlaying();
  playBtn.textContent = playing ? "❚❚" : "▶";
  playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
  const t = leader() ? leader().ws.getCurrentTime() : 0;
  nowStatus.textContent = playing ? "NOW PLAYING" : (t > 0.05 ? "PAUSED" : "LOADED");
}

playBtn.addEventListener("click", (e) => { togglePlay(); e.currentTarget.blur(); });
restartBtn.addEventListener("click", (e) => { restart(); e.currentTarget.blur(); });

// ---------------------------------------------------------------------------
// 5. A–B section + loop
// ---------------------------------------------------------------------------
function applySectionFromRegion(region) {
  if (syncing) return;
  loop.start = region.start;
  loop.end = region.end;
  reflectRegions(region);
  updateSectionReadout();
}

// mirror the single selection across all three waveforms. Guarded by `syncing`
// so the region-created/updated events this fires don't recurse back in.
function reflectRegions(source) {
  syncing = true;
  try {
    channels.forEach((c) => {
      c.regions.getRegions().forEach((r) => { if (r !== source) r.remove(); });
      if (!c.regions.getRegions().includes(source)) {
        c.regions.addRegion({
          start: loop.start, end: loop.end,
          color: REGION_COLOR, drag: true, resize: true,
        });
      }
    });
  } finally {
    syncing = false;
  }
}

// Programmatic (keyboard) region: clear everything and lay down fresh bounds.
function renderRegion() {
  syncing = true;
  try {
    channels.forEach((c) => {
      c.regions.getRegions().forEach((r) => r.remove());
      c.regions.addRegion({
        start: loop.start, end: loop.end,
        color: REGION_COLOR, drag: true, resize: true,
      });
    });
  } finally {
    syncing = false;
  }
  updateSectionReadout();
}

const MIN_SECTION = 0.1;   // shortest A–B section we allow, seconds

function setLoopStart(t) {
  t = clamp(t);
  if (loop.end == null || loop.end - t < MIN_SECTION) loop.end = clamp(t + 2);
  // guard against the extremes clamping start and end together
  loop.start = Math.min(t, loop.end - MIN_SECTION);
  renderRegion();
}

function setLoopEnd(t) {
  t = clamp(t);
  if (loop.start == null || t - loop.start < MIN_SECTION) loop.start = clamp(t - 2);
  loop.end = Math.max(t, loop.start + MIN_SECTION);
  renderRegion();
}

function nudgeBoundary(which, delta) {
  if (loop.start == null) return;
  if (which === "start") loop.start = Math.min(clamp(loop.start + delta), loop.end - 0.05);
  else loop.end = Math.max(clamp(loop.end + delta), loop.start + 0.05);
  renderRegion();
}

function updateSectionReadout() {
  if (loop.start == null) { sectionEl.hidden = true; return; }
  const len = (loop.end - loop.start).toFixed(1);
  sectionEl.innerHTML = `<b>A</b> ${fmt1(loop.start)} &nbsp; <b>B</b> ${fmt1(loop.end)} &nbsp; ${len}s`;
  sectionEl.hidden = false;
}

function setLoopEnabled(on) {
  loop.enabled = on;
  loopToggle.setAttribute("aria-pressed", String(on));
}

// CLEAR A–B just drops the section; the LOOP state is left alone (so it falls
// back to looping the whole song if LOOP is on).
function clearSection() {
  loop.start = null;
  loop.end = null;
  channels.forEach((c) => c.regions.clearRegions());
  updateSectionReadout();
}

loopToggle.addEventListener("click", () => setLoopEnabled(!loop.enabled));
document.getElementById("loop-clear").addEventListener("click", clearSection);

// ---------------------------------------------------------------------------
// 6. Speed + keyboard
// ---------------------------------------------------------------------------
const speedSel = document.getElementById("speed");
speedSel.addEventListener("change", () => {
  channels.forEach((c) => c.ws.setPlaybackRate(currentRate(), true));
});
function currentRate() { return parseFloat(speedSel.value); }

document.addEventListener("keydown", (e) => {
  // While a dialog is open, only Escape matters.
  if (!markerOverlay.hidden) { if (e.key === "Escape") closeMarker(); return; }
  if (!overlay.hidden) { if (e.key === "Escape") closeEditor(); return; }
  if (!helpOverlay.hidden) { if (e.key === "Escape") closeHelp(); return; }
  if (playerEl.hidden) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return;

  const t = leader() ? leader().ws.getCurrentTime() : 0;
  switch (e.key) {
    case " ":
      e.preventDefault(); togglePlay(); break;   // preventDefault also stops a focused button double-firing
    case "?":
      openHelp(); break;
    case "i": case "I":
      setLoopStart(t); break;
    case "o": case "O":
      setLoopEnd(t); break;
    case "l": case "L":
      setLoopEnabled(!loop.enabled); break;
    case "Backspace": case "Delete":
      e.preventDefault(); clearSection(); break;
    case "ArrowLeft":
      e.preventDefault();
      if (e.altKey) nudgeBoundary("start", -NUDGE);
      else if (e.shiftKey) nudgeBoundary("end", -NUDGE);
      else seekAll(clamp(t - SEEK));
      break;
    case "ArrowRight":
      e.preventDefault();
      if (e.altKey) nudgeBoundary("start", NUDGE);
      else if (e.shiftKey) nudgeBoundary("end", NUDGE);
      else seekAll(clamp(t + SEEK));
      break;
  }
});

// ---------------------------------------------------------------------------
// 6b. Waveform markers
// ---------------------------------------------------------------------------
const markerLayer = document.getElementById("marker-layer");
const markerOverlay = document.getElementById("marker-overlay");
const markerName = document.getElementById("marker-name");
const markerTimeEl = document.getElementById("marker-time");
const markerError = document.getElementById("marker-error");

function markerX(t) {
  const dur = duration() || 1;
  return phGeom.left + (Math.min(Math.max(0, t), dur) / dur) * phGeom.width;
}

function renderMarkers() {
  if (!markerLayer) return;
  markerLayer.replaceChildren();
  if (!phGeom) return;
  markers.forEach((m) => {
    const el = document.createElement("div");
    el.className = "marker";
    el.dataset.id = m.id;
    el.style.left = markerX(m.time) + "px";
    el.style.top = phGeom.top + "px";
    el.style.height = phGeom.height + "px";
    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "marker__i";
    badge.textContent = "i";
    badge.title = m.name || "Marker";
    badge.setAttribute("aria-label", "Marker: " + (m.name || "unnamed"));
    badge.addEventListener("click", (e) => { e.stopPropagation(); openMarker(m, false); });
    el.appendChild(badge);
    markerLayer.appendChild(el);
  });
}

function layoutMarkers() { if (phGeom) { renderMarkers(); positionCapture(); } }

async function loadMarkers() {
  try {
    const res = await fetch(`/api/jobs/${jobId}/markers`);
    if (res.ok) { markers = (await res.json()).markers || []; renderMarkers(); }
  } catch {}
}

async function persistMarkers() {
  try {
    const res = await fetch(`/api/jobs/${jobId}/markers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markers }),
    });
    if (res.ok) { markers = (await res.json()).markers || markers; return true; }
  } catch {}
  return false;
}

function openMarker(m, isNew) {
  editingMarkerId = m.id;
  editingIsNew = isNew;
  markerTimeEl.textContent = fmt1(m.time);
  markerName.value = m.name || "";
  markerError.textContent = "";
  markerOverlay.hidden = false;
  markerName.focus();
}

function closeMarker() {
  // Cancelling a brand-new, never-saved marker discards it.
  if (editingIsNew) {
    markers = markers.filter((x) => x.id !== editingMarkerId);
    renderMarkers();
  }
  markerOverlay.hidden = true;
  editingMarkerId = null;
}

async function saveMarker() {
  const m = markers.find((x) => x.id === editingMarkerId);
  if (!m) { markerOverlay.hidden = true; editingMarkerId = null; return; }
  const prev = m.name;
  m.name = markerName.value.trim();
  if (!(await persistMarkers())) {
    m.name = prev;
    markerError.textContent = "Couldn't save. Try again.";
    return;
  }
  editingIsNew = false;
  renderMarkers();
  markerOverlay.hidden = true;
  editingMarkerId = null;
}

async function deleteMarker() {
  const keep = markers.filter((x) => x.id !== editingMarkerId);
  const prev = markers;
  markers = keep;
  if (!(await persistMarkers())) {
    markers = prev;
    markerError.textContent = "Couldn't delete. Try again.";
    return;
  }
  editingIsNew = false;
  renderMarkers();
  markerOverlay.hidden = true;
  editingMarkerId = null;
}

document.getElementById("marker-save").addEventListener("click", saveMarker);
document.getElementById("marker-cancel").addEventListener("click", closeMarker);
document.getElementById("marker-delete").addEventListener("click", deleteMarker);
markerOverlay.addEventListener("click", (e) => { if (e.target === markerOverlay) closeMarker(); });

const markerOpenBtn = document.getElementById("marker-open");
const markerCapture = document.getElementById("marker-capture");
const markerTip = document.getElementById("marker-tip");

function positionCapture() {
  if (!phGeom || !markerMode) return;
  markerCapture.style.left = phGeom.left + "px";
  markerCapture.style.top = phGeom.top + "px";
  markerCapture.style.width = phGeom.width + "px";
  markerCapture.style.height = phGeom.height + "px";
}

function setMarkerMode(on) {
  markerMode = on;
  markerOpenBtn.classList.toggle("is-on", on);
  markerOpenBtn.setAttribute("aria-pressed", String(on));
  markerCapture.hidden = !on;
  if (on) positionCapture();
  else markerTip.hidden = true;
}

function timeAtClientX(clientX) {
  const base = document.getElementById("strips").getBoundingClientRect();
  const x = clientX - base.left - phGeom.left;
  const dur = duration() || 1;
  return Math.min(Math.max(0, (x / phGeom.width) * dur), dur);
}

markerOpenBtn.addEventListener("click", () => setMarkerMode(!markerMode));

markerCapture.addEventListener("click", (e) => {
  if (!phGeom) return;
  const m = { id: crypto.randomUUID(), time: timeAtClientX(e.clientX), name: "" };
  markers.push(m);
  renderMarkers();
  markerTip.hidden = true;
  openMarker(m, true);
});

markerCapture.addEventListener("mousemove", (e) => {
  if (!phGeom) return;
  const base = document.getElementById("strips").getBoundingClientRect();
  markerTip.textContent = fmt1(timeAtClientX(e.clientX));
  markerTip.style.left = (e.clientX - base.left) + "px";
  markerTip.style.top = (e.clientY - base.top - 10) + "px";
  markerTip.hidden = false;
});

markerCapture.addEventListener("mouseleave", () => { markerTip.hidden = true; });

// ---------------------------------------------------------------------------
// 7. Edit metadata
// ---------------------------------------------------------------------------
const overlay = document.getElementById("edit-overlay");
const editSong = document.getElementById("edit-song");
const editArtist = document.getElementById("edit-artist");
const editUrl = document.getElementById("edit-url");
const editError = document.getElementById("edit-error");
const editSave = document.getElementById("edit-save");

function openEditor() {
  if (!currentJob) return;
  editError.textContent = "";
  editSong.value = currentJob.title || "";
  editArtist.value = currentJob.artist || "";
  editUrl.value = currentJob.youtube_url || "";
  overlay.hidden = false;
  editSong.focus();
}
function closeEditor() { overlay.hidden = true; }

// Poll for the background thumbnail refresh after a URL change.
function watchThumbnail(previous) {
  let tries = 0;
  const iv = setInterval(async () => {
    tries++;
    try {
      const job = await (await fetch(`/api/jobs/${jobId}`)).json();
      if ((job.thumbnail_url || "") !== (previous || "")) {
        currentJob.thumbnail_url = job.thumbnail_url;
        setThumb(job.thumbnail_url);
        clearInterval(iv);
      }
    } catch (e) { /* keep trying */ }
    if (tries >= 8) clearInterval(iv);
  }, 2000);
}

async function saveEdit() {
  const title = editSong.value.trim();
  if (!title) { editSong.focus(); editError.textContent = "Enter a song name."; return; }
  editSave.disabled = true;
  editError.textContent = "";
  const prevUrl = currentJob.youtube_url || "";
  const prevThumb = currentJob.thumbnail_url || "";
  try {
    const res = await fetch(`/api/jobs/${jobId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        artist: editArtist.value.trim(),
        youtube_url: editUrl.value.trim(),
      }),
    });
    const job = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(job.detail || "Couldn't save changes.");
    currentJob = job;
    document.getElementById("track-title").textContent = job.title || "Untitled";
    document.getElementById("track-artist").textContent = job.artist || "";
    setThumb(job.thumbnail_url);
    closeEditor();
    // The thumbnail refresh runs in the background; watch for it to land.
    if ((job.youtube_url || "") !== prevUrl) watchThumbnail(prevThumb);
  } catch (err) {
    editError.textContent = err.message;
  } finally {
    editSave.disabled = false;
  }
}

document.getElementById("edit-open").addEventListener("click", openEditor);
document.getElementById("edit-cancel").addEventListener("click", closeEditor);
editSave.addEventListener("click", saveEdit);
overlay.addEventListener("click", (e) => { if (e.target === overlay) closeEditor(); });

// ---------------------------------------------------------------------------
// 8. Keyboard shortcuts help
// ---------------------------------------------------------------------------
const helpOverlay = document.getElementById("help-overlay");
function openHelp() { helpOverlay.hidden = false; }
function closeHelp() { helpOverlay.hidden = true; }
document.getElementById("help-open").addEventListener("click", openHelp);
document.getElementById("help-close").addEventListener("click", closeHelp);
helpOverlay.addEventListener("click", (e) => { if (e.target === helpOverlay) closeHelp(); });

// Delete recording
document.getElementById("delete-open").addEventListener("click", async () => {
  if (!confirm("Delete this recording? It's removed from your library. " +
               "The audio files stay on disk.")) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (res.status === 204) { location.href = "/"; return; }
    const d = await res.json().catch(() => ({}));
    alert(d.detail || "Could not delete this recording.");
  } catch (err) {
    alert("Could not delete this recording: " + err.message);
  }
});

// ---------------------------------------------------------------------------
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}
function fmt1(sec) {
  sec = Math.max(0, sec || 0);
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1).padStart(4, "0");
  return `${m}:${s}`;
}

poll();
