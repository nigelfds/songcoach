// Landing page: tap system audio, then hand the capture to the pipeline.
const btn = document.getElementById("capture-btn");
const label = btn.querySelector(".btn-rec__label");
const led = document.getElementById("rec-led");
const stateEl = document.getElementById("tape-state");
const timerEl = document.getElementById("tape-timer");
const error = document.getElementById("form-error");
const song = document.getElementById("song");
const artist = document.getElementById("artist");
const yturl = document.getElementById("yturl");
const ytLoadBtn = document.getElementById("yt-load-btn");
const ytStatus = document.getElementById("yt-status");
const ytEmbed = document.getElementById("yt-embed");
const metaInputs = [song, artist, yturl];

const TAIL_MS = 400;      // keep capturing briefly after the video ends

let recording = false;
let busy = false;
let timerId = null;
let startedAt = 0;

// YouTube IFrame player state
let player = null;        // YT.Player instance (once the API is ready)
let ytApiReady = false;
let pendingVideoId = null;
let currentVideoId = null;
let embeddable = true;    // flips false if the player reports it can't embed
let ending = false;       // guard so ENDED only auto-stops once

function setState(rec) {
  recording = rec;
  btn.dataset.recording = String(rec);
  led.dataset.on = String(rec);
  label.textContent = rec ? "STOP & SEPARATE" : "START CAPTURE";
  stateEl.textContent = rec ? "Recording…" : "Ready to capture";
  timerEl.hidden = !rec;
  metaInputs.forEach((el) => { el.disabled = rec; });
  ytLoadBtn.disabled = rec;
}

// ---------------------------------------------------------------------------
// YouTube: clean the pasted link, pull the title/artist, load the embed
// ---------------------------------------------------------------------------
function setYtStatus(msg, isError = false) {
  ytStatus.textContent = msg || "";
  ytStatus.hidden = !msg;
  ytStatus.classList.toggle("yt-status--error", isError);
}

let lastLoaded = "";
let ytBusy = false;
async function loadYouTube() {
  if (recording || ytBusy) return;
  const url = yturl.value.trim();
  if (!url) { setYtStatus(""); return; }
  ytBusy = true;
  ytLoadBtn.disabled = true;
  setYtStatus("Loading…");
  try {
    const res = await fetch(`/api/youtube/meta?url=${encodeURIComponent(url)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Couldn't read that link.");
    yturl.value = data.canonical_url;      // strip tracking/playlist params
    lastLoaded = data.canonical_url;
    if (data.song) song.value = data.song;
    if (data.artist) artist.value = data.artist;
    embeddable = true;
    currentVideoId = data.video_id;
    ytEmbed.hidden = false;                // reveal before building the player so it has size
    cueVideo(currentVideoId);
    setYtStatus(data.title ? `Loaded “${data.title}”` : "Video loaded — hit start to play & capture.");
  } catch (err) {
    setYtStatus(err.message, true);
  } finally {
    ytBusy = false;
    ytLoadBtn.disabled = recording;
  }
}

ytLoadBtn.addEventListener("click", loadYouTube);
yturl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); loadYouTube(); } });
// Paste-and-go: load once the pasted value has settled.
yturl.addEventListener("paste", () => setTimeout(loadYouTube, 0));
// Also load on blur if the field changed and hasn't been loaded yet.
yturl.addEventListener("blur", () => { if (yturl.value.trim() && yturl.value.trim() !== lastLoaded) loadYouTube(); });

// ---------------------------------------------------------------------------
// YouTube IFrame API: play the embed on capture, detect when it ends
// ---------------------------------------------------------------------------
(function loadYouTubeApi() {
  const s = document.createElement("script");
  s.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(s);
})();

// The API invokes this global once it has loaded.
window.onYouTubeIframeAPIReady = () => {
  ytApiReady = true;
  if (pendingVideoId) { cueVideo(pendingVideoId); pendingVideoId = null; }
};

function cueVideo(id) {
  if (!ytApiReady) { pendingVideoId = id; return; }  // build it once the API arrives
  if (!player) {
    player = new YT.Player("yt-iframe", {
      videoId: id,
      playerVars: { rel: 0, modestbranding: 1, playsinline: 1, origin: location.origin },
      events: { onStateChange: onPlayerState, onError: onPlayerError },
    });
  } else {
    player.cueVideoById(id);   // load, don't autoplay
  }
}

function videoReady() { return !!player && !!currentVideoId && embeddable; }

function playLoadedVideo() {
  if (!videoReady()) return;
  ending = false;
  try { player.unMute(); player.setVolume(100); player.playVideo(); } catch {}
}

function onPlayerState(e) {
  // ENDED (0): the song finished → stop capture after a short tail so syscap
  // flushes the last of the audio.
  if (e.data === YT.PlayerState.ENDED && recording && !ending) {
    ending = true;
    setTimeout(() => triggerStop({ auto: true }), TAIL_MS);
  }
}

function onPlayerError() {
  // e.g. the owner disabled embedding — auto play/stop can't work here.
  embeddable = false;
  setYtStatus("This video can't be embedded — play the source yourself, then stop.", true);
}

function fmt(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function startTimer() {
  startedAt = Date.now();
  timerEl.textContent = "0:00";
  timerId = setInterval(() => { timerEl.textContent = fmt(Date.now() - startedAt); }, 250);
}
function stopTimer() {
  clearInterval(timerId);
  timerId = null;
}

async function begin() {
  const title = song.value.trim();
  if (!title) {
    song.focus();
    throw new Error("Enter a song name first.");
  }
  const res = await fetch("/api/recordings/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      artist: artist.value.trim(),
      youtube_url: yturl.value.trim(),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Couldn't start capture.");
  setState(true);
  startTimer();
}

async function end() {
  const res = await fetch("/api/recordings/stop", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Couldn't stop capture.");
  stopTimer();
  stateEl.textContent = "Separating…";
  window.location.href = `/jobs/${data.id}`;
}

async function triggerStart() {
  if (busy || recording) return;
  busy = true;
  btn.disabled = true;
  error.textContent = "";
  try {
    await begin();          // start capturing first…
    playLoadedVideo();      // …then, if a video is loaded, unmute + play it
  } catch (err) {
    error.textContent = err.message;
    stopTimer();
    setState(false);
  } finally {
    busy = false;
    btn.disabled = false;
  }
}

async function triggerStop({ auto = false } = {}) {
  if (busy || !recording) return;
  busy = true;
  btn.disabled = true;
  error.textContent = "";
  try {
    // A manual stop also pauses the source; an auto stop follows the video ending.
    if (player && !auto) { try { player.pauseVideo(); } catch {} }
    await end();            // stop capture → redirect to the player
  } catch (err) {
    error.textContent = err.message;
    stopTimer();
    setState(false);
  } finally {
    busy = false;
    btn.disabled = false;
  }
}

btn.addEventListener("click", () => (recording ? triggerStop() : triggerStart()));

// A capture may already be running (e.g. the page was reloaded mid-record) —
// reflect that so the button offers Stop rather than a second Start.
(async () => {
  try {
    const res = await fetch("/api/recordings/status");
    const { recording: active } = await res.json();
    if (active) {
      setState(true);
      startTimer(); // elapsed restarts from 0; the server keeps the real clock
    }
  } catch {}
})();
