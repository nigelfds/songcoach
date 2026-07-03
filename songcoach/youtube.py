"""YouTube URL helpers + a public oEmbed lookup (no API key, no yt-dlp).

Parses the id out of the common URL shapes, canonicalises to a bare
``watch?v=<id>`` link, and pulls the title/author from YouTube's oEmbed
endpoint so the capture form can pre-fill the song and artist.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Trailing decorations we strip off a video title to recover the song name,
# e.g. "Song (Official Video)", "Song [HD]", "Song (Lyrics)".
_DECOR_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b("
    r"official|video|audio|lyrics?|hd|4k|mv|visuali[sz]er|remaster(?:ed)?|"
    r"explicit|full|live|cover|extended|version"
    r")\b[^\)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)
# Separators between "Artist" and "Song" in a typical music-video title.
_TITLE_SEPS = (" - ", " – ", " — ", " · ", " ~ ")


def video_id(url: str) -> str | None:
    """Extract the 11-char video id from the common YouTube URL shapes."""
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host

    if host == "youtu.be":
        cand = u.path.lstrip("/").split("/")[0]
        return cand if _ID_RE.match(cand) else None
    if host.endswith("youtube.com"):
        if u.path == "/watch":
            v = parse_qs(u.query).get("v", [None])[0]
            return v if v and _ID_RE.match(v) else None
        parts = u.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "v", "live"):
            return parts[1] if _ID_RE.match(parts[1]) else None
    return None


def canonical_url(vid: str) -> str:
    """A bare watch URL with everything after the id stripped off."""
    return f"https://www.youtube.com/watch?v={vid}"


def embed_url(vid: str) -> str:
    return f"https://www.youtube.com/embed/{vid}"


def _strip_decorations(name: str) -> str:
    prev = None
    while prev != name:
        prev = name
        name = _DECOR_RE.sub("", name).strip()
    return name


def _split_title(title: str, author: str) -> tuple[str, str]:
    """Best-effort (song, artist) from a video title + channel name."""
    if not title:
        return "", author
    for sep in _TITLE_SEPS:
        if sep in title:
            artist, song = title.split(sep, 1)
            return _strip_decorations(song), artist.strip()
    # No separator: treat the whole title as the song, channel as the artist.
    return _strip_decorations(title), author


def _oembed(vid: str) -> dict | None:
    query = urlencode({"url": canonical_url(vid), "format": "json"})
    req = Request(
        f"https://www.youtube.com/oembed?{query}",
        headers={"User-Agent": "SongCoach/1.0"},
    )
    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def lookup(url: str) -> dict | None:
    """Resolve a pasted URL to ids + a best-effort song/artist.

    Returns ``None`` only when no video id can be parsed. If the network
    lookup fails the ids/embed are still returned (song/artist just empty),
    so the user can play the video and type the names in themselves.
    """
    vid = video_id(url)
    if not vid:
        return None
    data = _oembed(vid) or {}
    title = (data.get("title") or "").strip()
    author = (data.get("author_name") or "").strip()
    author = re.sub(r"\s*-\s*Topic$", "", author).strip()  # auto-generated artist channels
    song, artist = _split_title(title, author)
    return {
        "video_id": vid,
        "canonical_url": canonical_url(vid),
        "embed_url": embed_url(vid),
        "title": title,
        "song": song,
        "artist": artist,
    }
