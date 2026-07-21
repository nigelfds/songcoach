"""Split audio into drums / no-drums stems with Demucs, in-process.

We drive Demucs through its Python building blocks (no ``python -m demucs``
subprocess) so this works inside a frozen .app, where there is no separate
interpreter to shell out to. The model is loaded once and reused.

This mirrors the old ``--two-stems drums --mp3 --mp3-bitrate 256`` CLI: separate
into the model's sources, keep ``drums``, and sum the rest into ``no_drums``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import torch
from demucs.apply import apply_model
from demucs.audio import AudioFile, save_audio
from demucs.pretrained import get_model

from ..config import settings

log = logging.getLogger("songcoach.separator")

_MP3_BITRATE = 256

_model = None
_model_lock = Lock()


@dataclass
class SeparationResult:
    drums_path: Path
    no_drums_path: Path


def _load_model():
    """Load and cache the Demucs model — heavy, so done once and reused."""
    global _model
    with _model_lock:
        if _model is None:
            log.info("Loading Demucs model %s", settings.demucs_model)
            model = get_model(settings.demucs_model)
            model.cpu()
            model.eval()
            _model = model
    return _model


def separate(audio_path: Path, out_dir: Path) -> SeparationResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    model = _load_model()

    # Read + normalise exactly as demucs.separate does.
    wav = AudioFile(audio_path).read(
        streams=0, samplerate=model.samplerate, channels=model.audio_channels
    )
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / ref.std()

    with torch.no_grad():
        sources = apply_model(
            model, wav[None], device="cpu",
            shifts=1, split=True, overlap=0.25, progress=False, num_workers=0,
        )[0]
    sources = sources * ref.std() + ref.mean()

    # Two stems: keep `drums`, sum every other source into `no_drums`.
    by_name = dict(zip(model.sources, sources))
    if "drums" not in by_name:
        raise RuntimeError(f"model {settings.demucs_model} has no 'drums' source")
    drums = by_name["drums"]
    no_drums = torch.zeros_like(drums)
    for name, tensor in by_name.items():
        if name != "drums":
            no_drums = no_drums + tensor

    drums_path = out_dir / "drums.mp3"
    no_drums_path = out_dir / "no_drums.mp3"
    save_audio(drums, str(drums_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)
    save_audio(no_drums, str(no_drums_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)

    if not drums_path.exists() or not no_drums_path.exists():
        raise RuntimeError("Demucs produced no stem files")
    return SeparationResult(drums_path=drums_path, no_drums_path=no_drums_path)
