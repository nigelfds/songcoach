import torch

from songcoach.pipeline import separator


class FakeModel:
    sources = ["drums", "bass", "other", "vocals"]
    samplerate = 44100
    audio_channels = 2


def test_separate_extracts_drums_vocals_backing(monkeypatch, tmp_path):
    monkeypatch.setattr(separator, "_load_model", lambda: FakeModel())

    # A standardized wav so separate()'s normalize/de-normalize is an identity
    # (ref.mean()~0, ref.std()~1), letting us assert on the raw source tensors.
    v = torch.linspace(-1, 1, 200)
    v = (v - v.mean()) / v.std()
    wav = torch.stack([v, v])                       # (channels=2, samples=200)

    class FakeAF:
        def __init__(self, p): pass
        def read(self, **kw): return wav
    monkeypatch.setattr(separator, "AudioFile", FakeAF)

    drums = torch.full((2, 200), 1.0)
    bass = torch.full((2, 200), 2.0)
    other = torch.full((2, 200), 3.0)
    vocals = torch.full((2, 200), 4.0)
    stacked = torch.stack([drums, bass, other, vocals])          # order == FakeModel.sources
    monkeypatch.setattr(separator, "apply_model", lambda *a, **k: stacked[None])

    saved = {}
    def fake_save(tensor, path, **kw):
        from pathlib import Path
        saved[Path(path).name] = tensor
        Path(path).write_bytes(b"x")
    monkeypatch.setattr(separator, "save_audio", fake_save)

    res = separator.separate(tmp_path / "in.mp3", tmp_path / "out")

    assert res.drums_path.name == "drums.mp3"
    assert res.vocals_path.name == "vocals.mp3"
    assert res.backing_path.name == "no_drums_no_vocals.mp3"
    assert torch.allclose(saved["drums.mp3"], drums)
    assert torch.allclose(saved["vocals.mp3"], vocals)
    assert torch.allclose(saved["no_drums_no_vocals.mp3"], bass + other)   # backing = bass+other
