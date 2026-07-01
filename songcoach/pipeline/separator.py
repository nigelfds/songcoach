"""Split audio into drums / no-drums stems with Demucs.

We shell out to the Demucs CLI with ``--two-stems=drums`` which is much faster
and lighter than a full 4-source separation and gives us exactly the two stems
we need (drums, and everything-but-drums). Demucs writes mp3 directly with
``--mp3`` so no extra transcode is required for those two.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import settings


@dataclass
class SeparationResult:
    drums_path: Path
    no_drums_path: Path


def separate(audio_path: Path, out_dir: Path) -> SeparationResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    model = settings.demucs_model

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems", "drums",
        "-n", model,
        "--mp3", "--mp3-bitrate", "256",
        "-o", str(out_dir),
        str(audio_path),
    ]
    # Raises CalledProcessError with stderr surfaced on failure.
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    # Demucs writes: {out_dir}/{model}/{track_stem}/{drums,no_drums}.mp3
    track_stem = audio_path.stem
    stem_dir = out_dir / model / track_stem
    drums = stem_dir / "drums.mp3"
    no_drums = stem_dir / "no_drums.mp3"
    if not drums.exists() or not no_drums.exists():
        found = list(stem_dir.glob("*")) if stem_dir.exists() else []
        raise RuntimeError(f"Demucs output missing. Found: {found}")

    return SeparationResult(drums_path=drums, no_drums_path=no_drums)
