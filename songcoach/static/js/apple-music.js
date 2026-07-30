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

function amRender(s) {
  const active = !!s.active;
  amStart.hidden = active;
  amStop.hidden = !active;
  amLed.dataset.on = active && s.phase !== "armed" ? "true" : "false";
  if (amBack) amBack.disabled = active;           // no leaving mid-session
  amPerm.hidden = !s.permission_error;

  let label = active ? PHASE_LABEL[s.phase] || "Active" : "Not started";
  if (active && s.current && (s.phase === "capturing" || s.phase === "paused")) {
    const who = s.current.artist ? ` — ${s.current.artist}` : "";
    label += `: ${s.current.name || "Unknown"}${who}`;
  }
  amState.textContent = label;

  amCaptured.innerHTML = "";
  (s.captured || []).forEach((c) => {
    const li = document.createElement("li");
    li.textContent = c.artist ? `${c.title} · ${c.artist}` : c.title || "Untitled";
    amCaptured.appendChild(li);
  });
}

async function amPoll() {
  try {
    const s = await (await fetch("/api/apple-music/status")).json();
    amRender(s);
    if (!s.active && amPollId) { clearInterval(amPollId); amPollId = null; }
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
    if (amPollId) { clearInterval(amPollId); amPollId = null; }
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
