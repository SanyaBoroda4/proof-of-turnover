"""pot.voice: neural speech cache and worker-loop synthesis."""

from types import SimpleNamespace

import pytest

from pot import voice as voice_mod


def _install_fake_edge(monkeypatch):
    calls = []

    class FakeCommunicate:
        def __init__(self, text, voice):
            calls.append((text, voice))

        async def stream(self):
            yield {"type": "audio", "data": b"mp3-one"}
            yield {"type": "audio", "data": b"mp3-two"}

    monkeypatch.setattr(
        voice_mod, "edge_tts",
        SimpleNamespace(Communicate=FakeCommunicate))
    return calls


def test_synthesize_caches_audio(tmp_path, monkeypatch):
    monkeypatch.delenv("POT_VOICE", raising=False)
    calls = _install_fake_edge(monkeypatch)
    first = voice_mod.synthesize("Hola cuarto", "es", cache_dir=tmp_path)
    second = voice_mod.synthesize("Hola cuarto", "es", cache_dir=tmp_path)
    assert first == second == b"mp3-onemp3-two"
    assert calls == [("Hola cuarto", "es-MX-DaliaNeural")]
    assert len(list(tmp_path.glob("*.mp3"))) == 1


def test_synthesize_raises_when_off(tmp_path, monkeypatch):
    monkeypatch.setenv("POT_VOICE", "device")
    with pytest.raises(RuntimeError):
        voice_mod.synthesize("Hello", "en", cache_dir=tmp_path)
