"""Record one song as 1+ segments (pause/resume) concatenated into one file.

Apple Music mode pauses/resumes capture with Music's transport. syscap only
does start/stop, so a "pause" finalizes the current segment and a "resume"
starts a new one; finish() concatenates all segments into capture.m4a (dead-air
from the pause is not recorded). Segments share identical AAC params (same
syscap), so ffmpeg stream-copy concat is valid, with a re-encode fallback.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..config import settings
from . import recorder as recorder_mod
from .recorder import RecorderError, RecordingResult

log = logging.getLogger("songcoach.segmented_recorder")


class SegmentedRecorder:
    def __init__(self, out_dir: Path, *, max_seconds: int | None = None):
        self.out_dir = Path(out_dir)
        self.max_seconds = max_seconds
        self.seg_dir = self.out_dir / "segments"
        self.capture_path = self.out_dir / "capture.m4a"
        self._segments: list[Path] = []
        self._durations: list[float] = []
        self._active: recorder_mod.Recorder | None = None

    def start(self) -> None:
        if self._segments or self._active is not None:
            raise RecorderError("segmented recorder already started")
        self._begin_segment()

    def _begin_segment(self) -> None:
        idx = len(self._segments)
        seg = self.seg_dir / f"{idx:03d}"
        rec = recorder_mod.Recorder(seg, max_seconds=self.max_seconds)
        rec.start()
        self._active = rec
        self._segments.append(seg / "capture.m4a")

    def _end_segment(self) -> None:
        if self._active is None:
            return
        result = self._active.stop()
        self._durations.append(result.duration or 0.0)
        self._active = None

    def pause(self) -> None:
        self._end_segment()

    def resume(self) -> None:
        if self._active is not None:
            raise RecorderError("resume while a segment is active")
        self._begin_segment()

    def finish(self) -> RecordingResult:
        self._end_segment()
        segs = [p for p in self._segments if p.exists() and p.stat().st_size > 0]
        if not segs:
            raise RecorderError("no audio captured")
        if len(segs) == 1:
            segs[0].replace(self.capture_path)
        else:
            self._concat(segs, self.capture_path)
        duration = sum(self._durations) or None
        log.info("Song finalized: %d segment(s), %.1fs → %s",
                 len(segs), duration or 0.0, self.capture_path)
        return RecordingResult(audio_path=self.capture_path, duration=duration)

    def _concat(self, segs: list[Path], dest: Path) -> None:
        listfile = self.seg_dir / "concat.txt"
        listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in segs), encoding="utf-8")
        base = [settings.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile)]
        try:
            subprocess.run(base + ["-c", "copy", str(dest)],
                           check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            log.warning("concat -c copy failed; re-encoding to AAC")
            subprocess.run(base + ["-c:a", "aac", "-b:a", "256k", str(dest)],
                           check=True, capture_output=True, text=True)
