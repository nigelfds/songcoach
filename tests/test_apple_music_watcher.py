from songcoach.apple_music.watcher import MusicState, parse_music_line


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
