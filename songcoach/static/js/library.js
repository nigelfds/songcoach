// Export / import the whole data/ library. Backend enforces the recording guard
// (409) — we disable Export on load as a courtesy and surface any 409 as a toast.
const exportBtn = document.getElementById("export-btn");
const importBtn = document.getElementById("import-btn");
const importFile = document.getElementById("import-file");
const overlay = document.getElementById("io-overlay");
const overlayMsg = document.getElementById("io-overlay-msg");
const toast = document.getElementById("io-toast");

function showToast(msg, isError = false) {
  toast.textContent = msg;
  toast.classList.toggle("io-toast--error", isError);
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 4000);
}

// Courtesy disable of Export while a capture is running.
(async () => {
  try {
    const { recording } = await (await fetch("/api/recordings/status")).json();
    if (recording) exportBtn.disabled = true;
  } catch {}
})();

exportBtn?.addEventListener("click", () => {
  // Native browser download (free progress bar); a 409 lands as a downloaded
  // error body, so re-check status first for a friendly message.
  fetch("/api/recordings/status")
    .then((r) => r.json())
    .then(({ recording }) => {
      if (recording) return showToast("Stop the current recording first.", true);
      window.location = "/api/export";
    })
    .catch(() => { window.location = "/api/export"; });
});

importBtn?.addEventListener("click", () => importFile.click());

importFile?.addEventListener("change", async () => {
  const file = importFile.files[0];
  importFile.value = ""; // allow re-picking the same file later
  if (!file) return;
  if (!confirm("Merge these recordings into your library? Any with the same ID will be overwritten.")) return;

  overlayMsg.textContent = "Importing… this can take a minute.";
  overlay.hidden = false;
  try {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/import", { method: "POST", body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.detail || "Import failed.", true);
      return;
    }
    const total = (data.added || 0) + (data.updated || 0);
    showToast(`Imported ${total} recording${total === 1 ? "" : "s"}.`);
    setTimeout(() => window.location.reload(), 900);
  } catch (err) {
    showToast("Import failed: " + err.message, true);
  } finally {
    overlay.hidden = true;
  }
});
