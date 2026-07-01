import WaveSurfer from "https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.esm.js";
import RegionsPlugin from "https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.esm.js";
import TimelinePlugin from "https://unpkg.com/wavesurfer.js@7/dist/plugins/timeline.esm.js";

const app = document.getElementById("app");
const jobId = app.dataset.jobId;

// color = played (progress), dim = unplayed waveform, tuned for the light UI.
const KINDS = [
  { kind: "original", name: "FULL SONG", sub: "reference mix", color: "#6d45e6", dim: "#c9bcf5" },
  { kind: "drums",    name: "DRUMS",     sub: "the kit, solo", color: "#e8760a", dim: "#f6cf9f" },
  { kind: "no_drums", name: "NO DRUMS",  sub: "play along",    color: "#0e9e90", dim: "#a6ded7" },
];

const REGION_COLOR = "rgba(232,118,10,.16)";
const EPS = 0.03;

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
      showError(job.error || "Processing failed.");
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

function showError(msg) {
  const el = document.getElementById("processing-error");
  el.textContent = msg;
  el.hidden = false;
  document.getElementById("stage-tag").textContent = "FAILED";
  document.getElementById("processing-hint").hidden = true;
  document.getElementById("meter-fill").style.background = "var(--red)";
}

// ---------------------------------------------------------------------------
// 2. Build the player
// ---------------------------------------------------------------------------
const channels = [];      // { kind, ws, regions, el, playBtn }
let currentJob = null;    // latest job metadata (for the editor)
let activeIndex = 1;      // the audible track — default to DRUMS
let ready = 0;
// A–B section selected on the waveforms: null bounds = whole track.
let loop = { enabled: false, start: null, end: null };
let syncing = false;      // guard while we programmatically move the playheads

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

  KINDS.forEach((meta, i) => {
    const track = byKind[meta.kind];
    if (!track) return;

    const strip = document.createElement("section");
    strip.className = "strip";
    strip.dataset.kind = meta.kind;
    strip.dataset.active = String(i === activeIndex);
    strip.innerHTML = `
      <div class="strip__top">
        <div class="strip__id">
          <span class="strip__dot"></span>
          <div>
            <div class="strip__name">${meta.name}</div>
            <div class="strip__sub">${meta.sub}</div>
          </div>
        </div>
        <div class="strip__ctrls">
          <button class="strip__btn strip__restart" type="button" title="Back to start" aria-label="Restart">⏮</button>
          <button class="strip__btn strip__play" type="button" title="Play / pause" aria-label="Play">▶</button>
        </div>
      </div>
      <div class="wave"><div class="wave__loading">DECODING…</div></div>
      <div class="wave-tl"></div>`;
    stripsEl.appendChild(strip);

    const waveEl = strip.querySelector(".wave");
    const regions = RegionsPlugin.create();
    const timeline = TimelinePlugin.create({
      container: strip.querySelector(".wave-tl"),
      height: 14,
      style: { fontSize: "9px", color: "#8a857a" },
    });
    const ws = WaveSurfer.create({
      container: waveEl,
      height: 72,
      waveColor: meta.dim,
      progressColor: meta.color,
      cursorColor: "#1c1b18",
      cursorWidth: 1,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      dragToSeek: false,   // dragging selects a section; click seeks
      url: track.url,
      plugins: [regions, timeline],
    });

    ws.on("decode", () => {
      const loading = waveEl.querySelector(".wave__loading");
      if (loading) loading.remove();
      regions.enableDragSelection({ color: REGION_COLOR });
      if (++ready === channels.length) onAllReady();
    });

    // drag-select on ANY strip drives one shared A–B section
    regions.on("region-created", (r) => applySectionFromRegion(r));
    regions.on("region-updated", (r) => applySectionFromRegion(r));

    // click to seek; clicking a quiet track makes it the audible one
    ws.on("interaction", (newTime) => {
      if (i !== activeIndex) setActive(i, { keepPlaying: true });
      seekAll(newTime);
    });

    ws.on("timeupdate", (t) => {
      if (i !== activeIndex || syncing) return;
      syncOthers(t);
      handleBoundary(t);
      document.getElementById("cur").textContent = fmt(t);
    });

    ws.on("finish", () => updateStrips());

    strip.querySelector(".strip__play").addEventListener("click", () => playStrip(i));
    strip.querySelector(".strip__restart").addEventListener("click", () => restartStrip(i));

    channels.push({ kind: meta.kind, ws, regions, el: strip });
  });
}

function onAllReady() {
  processingEl.hidden = true;
  playerEl.hidden = false;
  const dur = channels[activeIndex].ws.getDuration();
  document.getElementById("dur").textContent = fmt(dur);
  channels.forEach((c) => c.ws.setPlaybackRate(currentRate(), true));
  updateStrips();
}

// ---------------------------------------------------------------------------
// 3. Transport — one track audible, all kept time-aligned
// ---------------------------------------------------------------------------
function updateStrips() {
  channels.forEach((c, idx) => {
    const active = idx === activeIndex;
    c.el.dataset.active = String(active);
    c.el.querySelector(".strip__play").textContent =
      active && c.ws.isPlaying() ? "❚❚" : "▶";
  });
}

function seekAll(t) {
  syncing = true;
  channels.forEach((c) => c.ws.setTime(t));
  syncing = false;
  document.getElementById("cur").textContent = fmt(t);
}

function syncOthers(t) {
  syncing = true;
  channels.forEach((c, idx) => { if (idx !== activeIndex) c.ws.setTime(t); });
  syncing = false;
}

function setActive(i, { keepPlaying = false } = {}) {
  if (i === activeIndex) return;
  const from = channels[activeIndex].ws;
  const wasPlaying = from.isPlaying();
  const t = from.getCurrentTime();
  from.pause();
  activeIndex = i;
  const to = channels[i].ws;
  to.setPlaybackRate(currentRate(), true);
  to.setTime(t);
  if (keepPlaying && wasPlaying) to.play();
  updateStrips();
}

// Play from the section start when we're outside (or at the end of) the section.
function startActive() {
  const ws = channels[activeIndex].ws;
  if (loop.start != null) {
    const t = ws.getCurrentTime();
    if (t < loop.start - 0.01 || t >= loop.end - EPS) seekAll(loop.start);
  }
  ws.play();
  updateStrips();
}

function toggleActive() {
  const ws = channels[activeIndex].ws;
  if (ws.isPlaying()) { ws.pause(); updateStrips(); }
  else startActive();
}

function playStrip(i) {
  if (i === activeIndex) { toggleActive(); return; }
  setActive(i, { keepPlaying: false });
  startActive();
}

function restartStrip(i) {
  if (i !== activeIndex) setActive(i, { keepPlaying: true });
  seekAll(loop.start != null ? loop.start : 0);
}

// At the section end: loop back if looping, otherwise stop and park at the start.
function handleBoundary(t) {
  if (loop.start == null || t < loop.end - EPS) return;
  if (loop.enabled) {
    seekAll(loop.start);
  } else {
    channels[activeIndex].ws.pause();
    seekAll(loop.start);
    updateStrips();
  }
}

// ---------------------------------------------------------------------------
// 4. A–B section + loop
// ---------------------------------------------------------------------------
function applySectionFromRegion(region) {
  if (syncing) return;
  loop.start = region.start;
  loop.end = region.end;
  reflectRegions(region);
  document.getElementById("loop-toggle").disabled = false;
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

const loopToggle = document.getElementById("loop-toggle");
loopToggle.addEventListener("click", () => {
  if (loop.start == null) return;   // nothing selected yet
  setLoopEnabled(!loop.enabled);
});
function setLoopEnabled(on) {
  loop.enabled = on;
  loopToggle.setAttribute("aria-pressed", String(on));
}

document.getElementById("loop-clear").addEventListener("click", () => {
  loop = { enabled: false, start: null, end: null };
  channels.forEach((c) => c.regions.clearRegions());
  setLoopEnabled(false);
  loopToggle.disabled = true;
});

// ---------------------------------------------------------------------------
// 5. Speed + keyboard
// ---------------------------------------------------------------------------
const speedSel = document.getElementById("speed");
speedSel.addEventListener("change", () => {
  channels.forEach((c) => c.ws.setPlaybackRate(currentRate(), true));
});
function currentRate() { return parseFloat(speedSel.value); }

// space = play/pause the audible track (but not while the edit dialog is open)
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && playerEl && !playerEl.hidden && overlay.hidden) {
    e.preventDefault();
    toggleActive();
  }
});

// ---------------------------------------------------------------------------
// 6. Edit metadata
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
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !overlay.hidden) closeEditor();
});

// ---------------------------------------------------------------------------
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

poll();
