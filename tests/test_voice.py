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


def test_prefetch_dedupes_and_on_demand_joins(tmp_path, monkeypatch):
    import threading

    monkeypatch.delenv("POT_VOICE", raising=False)
    started = []
    release = threading.Event()

    def fake_synth(text, voice):
        started.append(text)
        assert release.wait(10)
        return b"mp3:" + text.encode()

    monkeypatch.setattr(voice_mod, "_synthesize_sync", fake_synth)
    voice_mod.prefetch(["same", "same", "other"], "en", cache_dir=tmp_path)
    result = {}

    def demand():
        result["data"] = voice_mod.synthesize("same", "en", cache_dir=tmp_path)

    worker = threading.Thread(target=demand)
    worker.start()
    deadline = threading.Event()
    while not started and not deadline.wait(0.05):
        pass
    release.set()
    worker.join(10)
    assert result["data"] == b"mp3:same"
    assert started.count("same") == 1


def test_prefetch_order_and_limit(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.delenv("POT_VOICE", raising=False)
    started = []
    release = threading.Event()

    def fake_synth(text, voice):
        started.append(text)
        assert release.wait(10)
        return b"mp3:" + text.encode()

    monkeypatch.setattr(voice_mod, "_synthesize_sync", fake_synth)
    texts = ["one", "two", "three", "four", "five"]
    voice_mod.prefetch(texts, "en", cache_dir=tmp_path)
    deadline = time.time() + 5
    while len(started) < 3 and time.time() < deadline:
        time.sleep(0.02)
    assert started == ["one", "two", "three"]
    release.set()
    deadline = time.time() + 5
    while len(list(tmp_path.glob("*.mp3"))) < 5 and time.time() < deadline:
        time.sleep(0.02)
    assert started == texts
    assert len(list(tmp_path.glob("*.mp3"))) == 5


def test_synthesize_raises_when_off(tmp_path, monkeypatch):
    monkeypatch.setenv("POT_VOICE", "device")
    with pytest.raises(RuntimeError):
        voice_mod.synthesize("Hello", "en", cache_dir=tmp_path)
