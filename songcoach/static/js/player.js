import WaveSurfer from "https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.esm.js";
import RegionsPlugin from "https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.esm.js";

const app = document.getElementById("app");
const jobId = app.dataset.jobId;

const KINDS = [
  { kind: "original", name: "FULL SONG", sub: "reference mix", color: "#b48cff", dim: "#5b4d78" },
  { kind: "drums",    name: "DRUMS",     sub: "the kit, solo", color: "#ff9e40", dim: "#7a5326" },
  { kind: "no_drums", name: "NO DRUMS",  sub: "play along",    color: "#37d0c0", dim: "#256b64" },
];

const STAGE_LABEL = {
  queued: "QUEUED", downloading: "DOWNLOADING", separating: "SEPARATING",
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
const channels = [];      // { kind, ws, regions, el }
let activeIndex = 1;      // default to DRUMS
let ready = 0;
let loop = { enabled: false, start: null, end: null };
let syncing = false;

function initPlayer(job) {
  document.getElementById("track-title").textContent = job.title || "Untitled";
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
        <button class="strip__solo" type="button">${i === activeIndex ? "● LISTENING" : "SOLO"}</button>
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
      cursorColor: "#e9e4d8",
      cursorWidth: 1,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      dragToSeek: true,
      url: track.url,
      plugins: [regions],
    });

    ws.on("decode", () => {
      const loading = waveEl.querySelector(".wave__loading");
      if (loading) loading.remove();
      regions.enableDragSelection({ color: "rgba(255,158,64,.18)" });
      if (++ready === channels.length) onAllReady();
    });

    // region drag-select on ANY strip drives a single shared A–B loop
    regions.on("region-created", (r) => applyLoopFromRegion(r));
    regions.on("region-updated", (r) => applyLoopFromRegion(r));

    // interacting with a non-active waveform makes it the listening track
    ws.on("interaction", () => {
      if (i !== activeIndex) setActive(i);
    });

    ws.on("timeupdate", (t) => {
      if (i !== activeIndex) return;
      syncOthers(t);
      handleLoop(t);
      document.getElementById("cur").textContent = fmt(t);
    });

    ws.on("finish", () => setPlayIcon(false));

    strip.querySelector(".strip__solo").addEventListener("click", () => setActive(i));

    channels.push({ kind: meta.kind, ws, regions, el: strip });
  });
}

function onAllReady() {
  processingEl.hidden = true;
  playerEl.hidden = false;
  const dur = channels[activeIndex].ws.getDuration();
  document.getElementById("dur").textContent = fmt(dur);
  channels.forEach((c) => c.ws.setPlaybackRate(currentRate(), true));
}

// ---------------------------------------------------------------------------
// 3. Active-track (solo) switching — only one instance is audible
// ---------------------------------------------------------------------------
function setActive(i) {
  if (i === activeIndex) return;
  const from = channels[activeIndex];
  const to = channels[i];
  const wasPlaying = from.ws.isPlaying();
  const time = from.ws.getCurrentTime();

  from.ws.pause();
  activeIndex = i;
  to.ws.setTime(time);
  to.ws.setPlaybackRate(currentRate(), true);
  if (wasPlaying) to.ws.play();

  channels.forEach((c, idx) => {
    c.el.dataset.active = String(idx === i);
    c.el.querySelector(".strip__solo").textContent = idx === i ? "● LISTENING" : "SOLO";
  });
}

function syncOthers(t) {
  syncing = true;
  channels.forEach((c, idx) => { if (idx !== activeIndex) c.ws.setTime(t); });
  syncing = false;
}

// ---------------------------------------------------------------------------
// 4. A–B loop
// ---------------------------------------------------------------------------
function applyLoopFromRegion(region) {
  if (syncing) return;
  loop.start = region.start;
  loop.end = region.end;
  loop.enabled = true;
  reflectLoopRegions(region);
  setLoopToggle(true);
}

// mirror the single active region across all three waveforms
function reflectLoopRegions(source) {
  channels.forEach((c) => {
    c.regions.getRegions().forEach((r) => { if (r !== source) r.remove(); });
    if (!c.regions.getRegions().includes(source)) {
      c.regions.addRegion({
        start: loop.start, end: loop.end,
        color: "rgba(255,158,64,.14)", drag: true, resize: true,
      });
    }
  });
}

function handleLoop(t) {
  if (!loop.enabled || loop.start == null) return;
  if (t >= loop.end - 0.02) {
    channels[activeIndex].ws.setTime(loop.start);
  }
}

const loopToggle = document.getElementById("loop-toggle");
loopToggle.addEventListener("click", () => {
  if (loop.start == null) return;           // nothing selected yet
  setLoopToggle(!loop.enabled);
});
function setLoopToggle(on) {
  loop.enabled = on;
  loopToggle.setAttribute("aria-pressed", String(on));
}

document.getElementById("loop-clear").addEventListener("click", () => {
  loop = { enabled: false, start: null, end: null };
  channels.forEach((c) => c.regions.clearRegions());
  setLoopToggle(false);
});

// ---------------------------------------------------------------------------
// 5. Transport
// ---------------------------------------------------------------------------
const playBtn = document.getElementById("playpause");
playBtn.addEventListener("click", () => {
  const ws = channels[activeIndex].ws;
  if (ws.isPlaying()) { ws.pause(); setPlayIcon(false); }
  else { ws.play(); setPlayIcon(true); }
});
function setPlayIcon(playing) { playBtn.textContent = playing ? "❚❚" : "▶"; }

document.getElementById("rewind").addEventListener("click", () => {
  const ws = channels[activeIndex].ws;
  ws.setTime(loop.enabled && loop.start != null ? loop.start : 0);
});

const speedSel = document.getElementById("speed");
speedSel.addEventListener("change", () => {
  channels.forEach((c) => c.ws.setPlaybackRate(currentRate(), true));
});
function currentRate() { return parseFloat(speedSel.value); }

// keyboard: space = play/pause
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && playerEl && !playerEl.hidden) {
    e.preventDefault();
    playBtn.click();
  }
});

// ---------------------------------------------------------------------------
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

poll();
