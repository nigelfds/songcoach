"""Desktop launcher: run the SongCoach server and show it in a native window.

Starts uvicorn on a free localhost port in a background thread, waits for it to
answer, then opens a pywebview (WKWebView) window pointing at it. This is the
entry point for the packaged .app — running the UI inside our own window is what
lets the macOS "Screen & System Audio Recording" permission attach to SongCoach
itself rather than to Terminal.

    python -m songcoach.desktop
"""
from __future__ import annotations

import socket
import threading
import time
from urllib.request import urlopen

import uvicorn

from .paths import is_frozen


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"server did not come up at {url}")


def main() -> None:
    import webview  # deferred so the module imports without pywebview in dev

    from .main import app

    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    _wait_until_up(f"{base}/healthz")

    webview.create_window("SongCoach", base, width=1120, height=860, min_size=(760, 600))
    # debug=True enables the WKWebView inspector (right-click → Inspect Element) in
    # dev; off in the shipped app.
    webview.start(debug=not is_frozen())
    server.should_exit = True


if __name__ == "__main__":
    main()
