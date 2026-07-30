import subprocess
import sys

import pytest

from songcoach.apple_music.watcher import MusicState, parse_music_line


@pytest.mark.skipif(sys.platform != "darwin", reason="osacompile is macOS-only")
@pytest.mark.parametrize("script_name", ["_SCRIPT", "_ARTWORK_SCRIPT"])
def test_applescripts_compile(script_name, tmp_path):
    """Regression: the osascript blocks must actually compile.

    The watcher's poll script once used `st` as a variable — a reserved
    AppleScript word — so it failed to compile (-2741) on every poll and the
    mode never saw a playing state. osacompile is a pure compile check: no
    execution, no Apple events to Music, no permissions, no side effects.
    """
    import songcoach.apple_music.artwork as artwork_mod
    import songcoach.apple_music.watcher as watcher_mod

    script = {"_SCRIPT": watcher_mod._SCRIPT,
              "_ARTWORK_SCRIPT": artwork_mod._ARTWORK_SCRIPT}[script_name]
    out = tmp_path / "compiled.scpt"
    res = subprocess.run(["osacompile", "-o", str(out), "-e", script],
                         capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, f"AppleScript failed to compile: {res.stderr.strip()}"


def test_parse_not_running():
    assert parse_music_line("not running") == MusicState("closed")


def test_parse_stopped():
    assert parse_music_line("stopped") == MusicState("stopped")


def test_parse_playing_with_track():
    s = parse_music_line("playing\tPID123\tSong Name\tThe Artist")
    assert s == MusicState("playing", "PID123", "Song Name", "The Artist")


def test_parse_paused():
    s = parse_music_line("paused\tPID9\tB\tArt")
    assert s.state == "paused" and s.track_id == "PID9"


def test_fast_forwarding_normalizes_to_playing():
    s = parse_music_line("fast forwarding\tPID1\tX\tY")
    assert s.state == "playing" and s.track_id == "PID1"


def test_rewinding_normalizes_to_playing():
    assert parse_music_line("rewinding\tPID1\tX\tY").state == "playing"


def test_unparseable_is_closed():
    assert parse_music_line("garble").state == "closed"
    assert parse_music_line("").state == "closed"
    assert parse_music_line("playing\tonlytwo").state == "closed"


def test_empty_track_fields_become_none():
    s = parse_music_line("playing\tPID\t\t")
    assert s.track_id == "PID" and s.name is None and s.artist is None
