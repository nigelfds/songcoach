"""Process-wide holder for the single Apple Music mode session + watcher."""
from __future__ import annotations

import threading

from .. import recording
from .session import AppleMusicSession
from .watcher import MusicWatcher

_lock = threading.Lock()
_session: AppleMusicSession | None = None
_watcher: MusicWatcher | None = None


class ModeError(RuntimeError):
    """Raised when the mode can't start (busy) — mapped to 409 at the route."""


def start_mode() -> dict:
    global _session, _watcher
    with _lock:
        if _session is not None and _session.status()["active"]:
            raise ModeError("Apple Music mode is already running")
        if recording.is_recording():
            raise ModeError("Stop the current recording first")
        session = AppleMusicSession()
        watcher = MusicWatcher(on_state=session.on_state)
        session.start()
        try:
            watcher.start()
        except Exception:
            session.stop()   # clears the recording guard
            raise
        _session, _watcher = session, watcher
        return _status_locked()


def stop_mode() -> dict:
    global _session, _watcher
    with _lock:
        if _watcher is not None:
            _watcher.stop()
        if _session is not None:
            _session.stop()
        status = _status_locked()
        _session = _watcher = None
        return status


def status() -> dict:
    with _lock:
        return _status_locked()


def _status_locked() -> dict:
    if _session is None:
        return {"active": False, "phase": "armed", "current": None,
                "captured": [], "permission_error": False}
    data = _session.status()
    data["permission_error"] = bool(_watcher and _watcher.permission_error)
    return data


def _reset_for_tests() -> None:
    global _session, _watcher
    with _lock:
        if _watcher is not None:
            _watcher.stop()
        _session = _watcher = None
    recording.set_apple_music_active(False)
