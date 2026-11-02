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
from collections import deque
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


class _VoiceJob:
    """One in-flight synthesis, shared by prefetch and on-demand calls."""

    def __init__(self, key, text, voice, path):
        self.key = key
        self.text = text
        self.voice = voice
        self.path = path
        self.state = "queued"
        self.event = threading.Event()
        self.data = None
        self.error = None
        self.counts_prefetch = False


_jobs = {}
_pending = deque()
_jobs_lock = threading.Lock()
_prefetch_running = 0


def _cache_path(text, voice, cache_dir):
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{key}.mp3"


def _start_locked(job, counts_prefetch):
    global _prefetch_running
    job.state = "running"
    job.counts_prefetch = counts_prefetch
    if counts_prefetch:
        _prefetch_running += 1
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()


def _pump_locked():
    while _prefetch_running < 3 and _pending:
        job = _pending.popleft()
        if job.state == "queued":
            _start_locked(job, True)


def _run_job(job):
    global _prefetch_running
    try:
        if job.path.is_file():
            job.data = job.path.read_bytes()
        else:
            job.data = _synthesize_sync(job.text, job.voice)
            job.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = job.path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(job.data)
            os.replace(tmp, job.path)
    except Exception as exc:  # noqa: BLE001 - waiters receive the error
        job.error = exc
    with _jobs_lock:
        job.state = "done"
        job.event.set()
        if _jobs.get(job.key) is job:
            del _jobs[job.key]
        if job.counts_prefetch:
            _prefetch_running -= 1
        _pump_locked()


def _job_for(text, voice, path, on_demand):
    key = (str(path), voice, text)
    with _jobs_lock:
        job = _jobs.get(key)
        if job is None:
            job = _VoiceJob(key, text, voice, path)
            _jobs[key] = job
            if on_demand:
                _start_locked(job, False)
            else:
                _pending.append(job)
                _pump_locked()
        elif on_demand and job.state == "queued":
            try:
                _pending.remove(job)
            except ValueError:
                pass
            _start_locked(job, False)
        return job


def synthesize(text, lang, cache_dir=None):
    """MP3 bytes for text/lang, cached; raises when off or failing.

    If the same text is already being synthesized (for example by a
    prefetch), this waits for that one synthesis instead of starting a
    second one. A queued prefetch that has not started yet is promoted
    and synthesized right away, never left behind the prefetch queue.
    """
    if neural_off():
        raise RuntimeError("neural voice is off")
    voice = VOICES.get(lang, VOICES["en"])
    cache_dir = Path(cache_dir) if cache_dir else Path(config.VOICE_CACHE_DIR)
    path = _cache_path(text, voice, cache_dir)
    if path.is_file():
        return path.read_bytes()
    job = _job_for(text, voice, path, on_demand=True)
    if not job.event.wait(TIMEOUT_SECONDS + 5):
        raise TimeoutError("voice synthesis timed out")
    if job.error is not None:
        raise job.error
    return job.data if job.data is not None else path.read_bytes()


def prefetch(texts, lang, cache_dir=None):
    """Queue missing clips for background synthesis (order kept, max 3).

    Returns how many distinct missing texts were queued or already in
    flight. Cached texts and duplicates in `texts` are skipped.
    """
    if neural_off():
        raise RuntimeError("neural voice is off")
    voice = VOICES.get(lang, VOICES["en"])
    cache_dir = Path(cache_dir) if cache_dir else Path(config.VOICE_CACHE_DIR)
    seen = set()
    queued = 0
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        path = _cache_path(text, voice, cache_dir)
        if path.is_file():
            continue
        _job_for(text, voice, path, on_demand=False)
        queued += 1
    return queued
