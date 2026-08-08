"""Split audio into drums / vocals / backing stems with Demucs, in-process.

We drive Demucs through its Python building blocks (no ``python -m demucs``
subprocess) so this works inside a frozen .app, where there is no separate
interpreter to shell out to. The model is loaded once and reused.

Separate into the model's sources, keep ``drums`` and ``vocals``, and sum
the rest (bass, other) into ``backing``.
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
    vocals_path: Path
    backing_path: Path


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

    # Three stems: keep `drums` and `vocals`; sum the rest (bass+other) into backing.
    by_name = dict(zip(model.sources, sources))
    for required in ("drums", "vocals"):
        if required not in by_name:
            raise RuntimeError(f"model {settings.demucs_model} has no '{required}' source")
    drums = by_name["drums"]
    vocals = by_name["vocals"]
    backing = torch.zeros_like(drums)
    for name, tensor in by_name.items():
        if name not in ("drums", "vocals"):
            backing = backing + tensor

    drums_path = out_dir / "drums.mp3"
    vocals_path = out_dir / "vocals.mp3"
    backing_path = out_dir / "no_drums_no_vocals.mp3"
    save_audio(drums, str(drums_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)
    save_audio(vocals, str(vocals_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)
    save_audio(backing, str(backing_path), samplerate=model.samplerate, bitrate=_MP3_BITRATE)

    if not (drums_path.exists() and vocals_path.exists() and backing_path.exists()):
        raise RuntimeError("Demucs produced no stem files")
    return SeparationResult(drums_path=drums_path, vocals_path=vocals_path, backing_path=backing_path)
