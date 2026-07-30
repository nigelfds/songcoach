"""Best-effort cover artwork from Apple Music for a captured song.

At song-begin we ask Music (via osascript) to write the current track's artwork
to a file, then store it as the job's thumbnail. Any failure (no artwork,
Automation denied, bad bytes) is swallowed — the tile just stays blank.
"""
from __future__ import annotations

import logging
import subprocess
import threading

from .. import fetch_thumbnails
from ..pipeline.recorder import capture_dir

log = logging.getLogger("songcoach.apple_music.artwork")

# Writes the current track's raw artwork bytes to outPath; returns "ok"/"none".
_ARTWORK_SCRIPT = '''
on run argv
  set outPath to item 1 of argv
  tell application "Music"
    if not (exists current track) then return "none"
    if (count of artworks of current track) is 0 then return "none"
    set d to raw data of artwork 1 of current track
  end tell
  set fh to open for access (POSIX file outPath) with write permission
  set eof fh to 0
  write d to fh
  close access fh
  return "ok"
end run
'''


def _export_artwork(out_file) -> bool:
    try:
        res = subprocess.run(
            ["osascript", "-e", _ARTWORK_SCRIPT, str(out_file)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and res.stdout.strip() == "ok"


def fetch_and_store(job_id: str) -> None:
    out_file = capture_dir(job_id) / "artwork.dat"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if _export_artwork(out_file):
        fetch_thumbnails.store_image_from_file(job_id, out_file)
    out_file.unlink(missing_ok=True)


def fetch_artwork_async(job_id: str) -> None:
    threading.Thread(target=fetch_and_store, args=(job_id,), daemon=True).start()
