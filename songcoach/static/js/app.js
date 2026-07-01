// Landing page: submit a YouTube URL, create a job, go to the player.
const form = document.getElementById("load-form");
const input = document.getElementById("url");
const error = document.getElementById("form-error");
const btn = form.querySelector(".btn-load");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  error.textContent = "";
  btn.disabled = true;
  const label = btn.childNodes[btn.childNodes.length - 1]; // trailing text node
  label.textContent = "LOADING…";

  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value.trim() }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Something went wrong.");
    }
    window.location.href = `/jobs/${data.id}`;
  } catch (err) {
    error.textContent = err.message;
    btn.disabled = false;
    label.textContent = "LOAD";
  }
});
