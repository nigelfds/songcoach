"""Poll Apple Music's transport once per second via osascript.

Emits a MusicState per tick to a callback. The AppleScript is guarded so it
never launches Music. The first call triggers the one-time Automation TCC
prompt; a denial surfaces as permission_error.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("songcoach.apple_music.watcher")

_SCRIPT = '''
if application "Music" is running then
  tell application "Music"
    set pstate to (player state as text)
    if pstate is "stopped" then
      return "stopped"
    else
      set trk to current track
      return pstate & tab & (persistent ID of trk) & tab & (name of trk) & tab & (artist of trk)
    end if
  end tell
else
  return "not running"
end if
'''


@dataclass(frozen=True)
class MusicState:
    state: str                    # "playing" | "paused" | "stopped" | "closed"
    track_id: str | None = None
    name: str | None = None
    artist: str | None = None


def parse_music_line(raw: str) -> MusicState:
    raw = (raw or "").rstrip('\n')
    if raw == "not running":
        return MusicState("closed")
    if raw == "stopped":
        return MusicState("stopped")
    parts = raw.split("\t")
    if len(parts) >= 4:
        st, tid, name, artist = parts[0], parts[1], parts[2], parts[3]
        if st in ("fast forwarding", "rewinding"):
            st = "playing"
        if st in ("playing", "paused"):
            return MusicState(st, tid or None, name or None, artist or None)
    return MusicState("closed")   # fail safe → ARMED, never a false capture


class MusicWatcher:
    def __init__(self, on_state: Callable[[MusicState], None], *, interval: float = 1.0):
        self._on_state = on_state
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.permission_error = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="music-watcher", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            state = self._poll()
            if state is not None:
                try:
                    self._on_state(state)
                except Exception:  # noqa: BLE001 — a handler error must not kill the loop
                    log.exception("Apple Music state handler failed")
            if self._stop.wait(self._interval):
                break

    def _poll(self) -> MusicState | None:
        try:
            res = subprocess.run(["osascript", "-e", _SCRIPT],
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode != 0:
            err = (res.stderr or "")
            self.permission_error = ("-1743" in err) or ("Not author" in err)
            return None
        self.permission_error = False
        return parse_music_line(res.stdout)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
