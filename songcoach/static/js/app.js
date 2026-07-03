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
const ytIframe = document.getElementById("yt-iframe");
const metaInputs = [song, artist, yturl];

let recording = false;
let busy = false;
let timerId = null;
let startedAt = 0;

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
    ytIframe.src = data.embed_url;
    ytEmbed.hidden = false;
    setYtStatus(data.title ? `Loaded “${data.title}”` : "Video loaded — play it, then capture.");
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

btn.addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  btn.disabled = true;
  error.textContent = "";
  try {
    await (recording ? end() : begin());
  } catch (err) {
    error.textContent = err.message;
    stopTimer();
    setState(false);
  } finally {
    busy = false;
    btn.disabled = false;
  }
});

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
