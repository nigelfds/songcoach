// Apple Music mode: drive the auto-capture session and poll its status.
const amStart = document.getElementById("am-start");
const amStop = document.getElementById("am-stop");
const amState = document.getElementById("am-state");
const amLed = document.getElementById("am-led");
const amCaptured = document.getElementById("am-captured");
const amPerm = document.getElementById("am-perm");
const amBack = document.getElementById("back-btn");

let amPollId = null;

const PHASE_LABEL = {
  armed: "Waiting for Apple Music…",
  capturing: "● Capturing",
  paused: "❚❚ Paused",
};

// Per-song separation state → short chip label.
const AM_STATUS = {
  recording: "capturing…",
  queued: "queued",
  separating: "separating…",
  uploading: "finishing…",
  done: "done ✓",
  failed: "failed",
};

const amSettled = (c) => c.status === "done" || c.status === "failed";

function amRender(s) {
  const active = !!s.active;
  const captured = s.captured || [];
  amStart.hidden = active;
  amStop.hidden = !active;
  amLed.dataset.on = active && s.phase !== "armed" ? "true" : "false";
  if (amBack) amBack.disabled = active;           // no leaving mid-session
  amPerm.hidden = !s.permission_error;

  let label;
  if (active) {
    label = PHASE_LABEL[s.phase] || "Active";
    if (s.current && (s.phase === "capturing" || s.phase === "paused")) {
      const who = s.current.artist ? ` — ${s.current.artist}` : "";
      label += `: ${s.current.name || "Unknown"}${who}`;
    }
  } else if (captured.length) {
    const done = captured.filter((c) => c.status === "done").length;
    label = done === captured.length
      ? `All ${captured.length} stemmed ✓`
      : `Stemming… ${done} of ${captured.length} done`;
  } else {
    label = "Not started";
  }
  amState.textContent = label;

  amCaptured.replaceChildren();
  captured.forEach((c) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "am-song";
    name.textContent = c.artist ? `${c.title} · ${c.artist}` : c.title || "Untitled";
    const badge = document.createElement("span");
    badge.className = "am-song-status";
    badge.dataset.status = c.status || "";
    badge.textContent = AM_STATUS[c.status] || "…";
    li.append(name, badge);
    amCaptured.appendChild(li);
  });
}

async function amPoll() {
  try {
    const s = await (await fetch("/api/apple-music/status")).json();
    amRender(s);
    // Keep polling after Stop until every dispatched song has finished stemming,
    // so the list shows queued → separating → done live.
    const allDone = (s.captured || []).every(amSettled);
    if (!s.active && allDone && amPollId) { clearInterval(amPollId); amPollId = null; }
  } catch {}
}

function amStartPolling() {
  if (!amPollId) amPollId = setInterval(amPoll, 1500);
}

amStart?.addEventListener("click", async () => {
  amStart.disabled = true;
  try {
    const res = await fetch("/api/apple-music/start", { method: "POST" });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      amState.textContent = d.detail || "Could not start.";
      return;
    }
    amRender(await res.json());
    amStartPolling();
  } finally {
    amStart.disabled = false;
  }
});

amStop?.addEventListener("click", async () => {
  amStop.disabled = true;
  try {
    const res = await fetch("/api/apple-music/stop", { method: "POST" });
    amRender(await res.json());
    amStartPolling();   // keep polling so the dispatched songs' stem progress updates live
  } finally {
    amStop.disabled = false;
  }
});

// If the mode is already running (page reload), restore the panel.
(async () => {
  try {
    const s = await (await fetch("/api/apple-music/status")).json();
    if (s.active) {
      if (typeof selectMode === "function") selectMode("applemusic");
      amRender(s);
      amStartPolling();
    }
  } catch {}
})();
