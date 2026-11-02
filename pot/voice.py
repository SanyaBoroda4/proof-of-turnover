"""Read text aloud with Microsoft neural voices (edge-tts), cached on disk.

This is the one non-Meta runtime exception (see AGENTS.md): the phone's
own voice is the fallback when synthesis is unavailable. Synthesis runs
edge_tts.Communicate in its own event loop inside a worker thread with
an 8 s timeout; results are cached under data/voice_cache/. The env var
POT_VOICE=device turns the neural voice off entirely.
"""

import asyncio
import hashlib
import os
import threading
from pathlib import Path

from pot import config

try:
    import edge_tts
except ImportError:  # pragma: no cover - missing optional dependency
    edge_tts = None

VOICES = {
    "en": "en-GB-SoniaNeural",
    "es": "es-MX-DaliaNeural",
    "ru": "ru-RU-SvetlanaNeural",
}

TIMEOUT_SECONDS = 8


def neural_off():
    """True when the neural voice is turned off or unavailable."""
    return edge_tts is None or os.environ.get("POT_VOICE") == "device"


async def _collect(text, voice):
    chunks = []
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def _synthesize_sync(text, voice):
    """asyncio.run inside a worker thread; raises on timeout or failure."""
    outcome = {}

    def worker():
        try:
            outcome["data"] = asyncio.run(_collect(text, voice))
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(TIMEOUT_SECONDS)
    if thread.is_alive():
        raise TimeoutError(f"voice synthesis timed out after {TIMEOUT_SECONDS} s")
    if "error" in outcome:
        raise outcome["error"]
    data = outcome.get("data") or b""
    if not data:
        raise RuntimeError("voice synthesis returned no audio")
    return data


def synthesize(text, lang, cache_dir=None):
    """MP3 bytes for text/lang, cached; raises when off or failing."""
    if neural_off():
        raise RuntimeError("neural voice is off")
    voice = VOICES.get(lang, VOICES["en"])
    cache_dir = Path(cache_dir) if cache_dir else Path(config.VOICE_CACHE_DIR)
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    path = cache_dir / f"{key}.mp3"
    if path.is_file():
        return path.read_bytes()
    data = _synthesize_sync(text, voice)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return data
