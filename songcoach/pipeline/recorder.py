"""Capture macOS system audio via the bundled ``syscap`` ScreenCaptureKit helper.

``syscap`` (see ``native/syscap.swift``) records whatever is playing out of the
Mac's audio output to an ``.m4a``. We drive it as a subprocess: ``start()``
launches it writing to a file; ``stop()`` sends SIGINT so it finalises the file
cleanly, then we hand the ``.m4a`` to the same Demucs separation the old
download path used.

Requires the one-time macOS **Screen & System Audio Recording** permission
granted to the host process. macOS-only.
"""
from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger("songcoach.recorder")

# recorder.py → pipeline → songcoach → repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RecorderError(RuntimeError):
    """Raised when capture can't start or the helper fails to produce audio."""


@dataclass
class RecordingResult:
    audio_path: Path
    duration: float | None


def capture_dir(job_id: str) -> Path:
    """Where a job's captured audio lives until it's separated and published."""
    return Path(settings.local_storage_dir) / "recordings" / job_id


def _resolve_binary() -> Path:
    binary = Path(settings.syscap_bin)
    if not binary.is_absolute():
        binary = _PROJECT_ROOT / binary
    if not binary.exists():
        raise RecorderError(
            f"syscap binary not found at {binary}. Build it with: "
            "swiftc -O native/syscap.swift -o native/syscap"
        )
    return binary


def _probe_duration(path: Path) -> float | None:
    """Read the container's exact duration; fall back to None if ffprobe fails."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            check=True, capture_output=True, text=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


class Recorder:
    """Drives a single ``syscap`` capture: ``start()`` then ``stop()``.

    ``max_seconds`` is an optional safety cap; ``syscap`` stops itself when it
    elapses even if ``stop()`` is never called.
    """

    def __init__(self, out_dir: Path, *, max_seconds: int | None = None):
        self.out_dir = out_dir
        self.max_seconds = max_seconds
        self.audio_path = out_dir / "capture.m4a"
        self._proc: subprocess.Popen | None = None
        self._start_ts: float | None = None

    @property
    def is_recording(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self._proc is not None:
            raise RecorderError("recording already started")
        if sys.platform != "darwin":
            raise RecorderError("system-audio capture is only supported on macOS")

        binary = _resolve_binary()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.audio_path.unlink(missing_ok=True)

        cmd = [str(binary), str(self.audio_path)]
        if self.max_seconds:
            cmd.append(str(self.max_seconds))
        log.info("Starting syscap → %s", self.audio_path)
        # syscap writes the audio file itself and logs status to stderr.
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        self._start_ts = time.monotonic()

        # Fail fast: a missing TCC permission makes syscap exit within a beat.
        time.sleep(0.4)
        if self._proc.poll() is not None:
            err = (self._proc.stderr.read() if self._proc.stderr else "").strip()
            self._proc = None
            raise RecorderError(f"syscap exited immediately: {err or 'unknown error'}")

    def stop(self, timeout: float = 10.0) -> RecordingResult:
        if self._proc is None:
            raise RecorderError("recording was never started")

        proc = self._proc
        if proc.poll() is None:
            # SIGINT → syscap finalises the .m4a and exits 0.
            proc.send_signal(signal.SIGINT)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self._proc = None
            raise RecorderError("syscap did not stop within timeout (killed)")
        self._proc = None

        # A clean SIGINT stop exits 0; anything else is a real failure.
        if proc.returncode not in (0, -signal.SIGINT):
            raise RecorderError(
                f"syscap failed (exit {proc.returncode}): {(err or '').strip()[:200]}"
            )
        if not self.audio_path.exists() or self.audio_path.stat().st_size == 0:
            raise RecorderError("syscap produced no audio file")

        duration = _probe_duration(self.audio_path)
        if duration is None and self._start_ts is not None:
            duration = time.monotonic() - self._start_ts
        log.info("Capture saved %s (%.1fs)", self.audio_path, duration or 0.0)
        return RecordingResult(audio_path=self.audio_path, duration=duration)
