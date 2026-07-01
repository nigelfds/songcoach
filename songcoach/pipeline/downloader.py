"""Download a YouTube video's audio track with yt-dlp.

Supports YouTube Premium (ad-free, higher quality) via a cookies file. On
Heroku the cookie contents can be provided in the YTDLP_COOKIES config var; we
materialise them to a temp file at call time.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger("songcoach.downloader")


@dataclass
class DownloadResult:
    audio_path: Path
    title: str
    duration: float | None
    thumbnail: str | None


def _apply_cookie_opts(opts: dict) -> None:
    """Attach cookie options to a yt-dlp opts dict, if any are configured.

    Precedence: explicit file > raw contents (written to temp file) >
    read directly from a local browser's cookie store.
    """
    if settings.ytdlp_cookies_file:
        # Tolerate a configured-but-absent file (e.g. the default ./cookies.txt
        # before the user has created one) — fall through to no cookies.
        if Path(settings.ytdlp_cookies_file).is_file():
            opts["cookiefile"] = settings.ytdlp_cookies_file
        else:
            log.warning(
                "YTDLP_COOKIES_FILE=%s not found; downloading without cookies",
                settings.ytdlp_cookies_file,
            )
    elif settings.ytdlp_cookies:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tmp.write(settings.ytdlp_cookies)
        tmp.flush()
        opts["cookiefile"] = tmp.name
    elif settings.ytdlp_cookies_from_browser:
        # yt-dlp expects a tuple: (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (settings.ytdlp_cookies_from_browser,)


def probe(url: str) -> dict:
    """Fetch metadata without downloading (title, duration, thumbnail)."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    _apply_cookie_opts(opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "Untitled",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
    }


def download_audio(url: str, out_dir: Path) -> DownloadResult:
    """Download best audio and extract to a wav for separation."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "source.%(ext)s")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "wav"},
        ],
    }
    _apply_cookie_opts(opts)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    audio_path = out_dir / "source.wav"
    if not audio_path.exists():
        # fall back to whatever extension the postprocessor produced
        candidates = sorted(out_dir.glob("source.*"))
        if not candidates:
            raise RuntimeError("yt-dlp produced no audio file")
        audio_path = candidates[0]

    return DownloadResult(
        audio_path=audio_path,
        title=info.get("title") or "Untitled",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
    )
