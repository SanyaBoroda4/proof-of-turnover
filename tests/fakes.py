"""Shared fakes for first-look tests: tiny photos and a scriptable client."""

import math
import struct
import subprocess
import wave
from datetime import datetime
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe
from PIL import Image

from pot.meta_client import call_context
from pot.photos import Photo


def make_photos(directory, count):
    """Build count tiny Photo objects backed by real JPEG files."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    photos = []
    for i in range(1, count + 1):
        photo_id = f"P{i:02d}"
        check_path = directory / f"{photo_id}_check.jpg"
        overview_path = directory / f"{photo_id}_overview.jpg"
        Image.new("RGB", (16, 12), (i % 256, 100, 50)).save(overview_path, "JPEG")
        Image.new("RGB", (32, 24), (i % 256, 100, 50)).save(check_path, "JPEG")
        photos.append(
            Photo(
                id=photo_id,
                original_name=f"{photo_id}.jpg",
                taken_at=None,
                received_at=datetime.now(),
                check_path=check_path,
                overview_path=overview_path,
                width=32,
                height=24,
            )
        )
    return photos


def tone_clip(path, container="webm"):
    """A 1 s 440 Hz tone as webm/opus or mp4/aac, like browser audio."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix(".src.wav")
    with wave.open(str(raw), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"".join(
            struct.pack("<h", int(9999 * math.sin(2 * math.pi * 440 * t / 44100)))
            for t in range(44100)
        ))
    codec = {"webm": "libopus", "mp4": "aac"}[container]
    proc = subprocess.run(
        [get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(raw),
         "-c:a", codec, str(path)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-300:]
    raw.unlink()
    return path


def photo_ids_in(content):
    """Photo ids from the "Photo Pxx" label parts of a request."""
    ids = []
    for part in content:
        if part.get("type") == "input_text" and part.get("text", "").startswith("Photo "):
            ids.append(part["text"].split(" ", 1)[1])
    return ids


def image_count(content):
    return sum(1 for part in content if part.get("type") == "input_image")


def tiny_property():
    return {
        "id": "tiny",
        "name": "Tiny",
        "cleaner_language": "es",
        "host_language": "en",
        "rooms": [
            {
                "id": "bed",
                "type": "bedroom",
                "name": "Bed",
                "floor": 1,
                "beds": 1,
                "description": "oak headboard",
            },
            {
                "id": "bath",
                "type": "bathroom",
                "name": "Bath",
                "floor": 1,
                "towels": 2,
                "description": "grey tiles",
            },
        ],
        "extra_items": [],
    }


def _unknown_everything(call):
    return {
        "matches": [
            {"photo_id": pid, "room_id": "unknown", "confidence": "low", "seen": ""}
            for pid in photo_ids_in(call["content"])
        ]
    }


def _not_shown_everything(call):
    enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
    return {
        "claims": [
            {"item_id": item_id, "status": "not_shown", "photo_id": "", "seen": "", "count_seen": None}
            for item_id in enum
        ],
        "other_issues": [],
    }


def _default_opinion(call):
    return {
        "evidence": "The photo shows the item is not done.",
        "requirement_met": "not_met",
    }


def _confirm_everything(call):
    return {
        "this_room": "yes",
        "verdict": "done",
        "count_seen": 5,
        "reason": "looks good",
        "problem_object": "",
    }


def _decide_first(call):
    enum = call["schema"]["properties"]["room_id"]["enum"]
    return {"room_id": enum[0]}


def _request_everything(call):
    enum = call["schema"]["properties"]["requests"]["items"]["properties"]["item_id"]["enum"]
    return {
        "requests": [
            {"item_id": item_id, "cleaner": f"Foto {item_id}", "host": f"Photo {item_id}"}
            for item_id in enum
        ]
    }


def _empty_tour(call):
    return {"shots": []}


def _keep_all_boxes(call):
    import re

    match = re.search(r"(\d+) numbered boxes", call["instructions"] or "")
    count = int(match.group(1)) if match else 0
    return {"boxes": list(range(1, count + 1))}


def _spanish_note(call):
    return {"transcript": "se acabó el café", "turns": []}


def _english_note(call):
    texts = [part.get("text", "") for part in call["content"]
             if part.get("type") == "input_text"]
    words = " ".join(texts).split()
    return {
        "translation": "EN: " + " ".join(texts),
        "title": " ".join(words[:5]) or "Note",
        "category": "supplies",
    }


def _empty_draft(call):
    return {
        "description": "a plain room",
        "beds": [],
        "towels": 0,
        "items": [],
        "unclear_rules": [],
    }


def _echo_rule(call):
    texts = [part.get("text", "") for part in call["content"]
             if part.get("type") == "input_text"]
    return {"english": "EN: " + " ".join(texts)}


SAM_FIXTURE = (
    Path(__file__).with_name("fixtures") / "sam_three_squares.txt"
).read_text(encoding="utf-8")


class FakeSpark:
    """Duck-typed stand-in for MetaClient.respond()/segment_image()."""

    def __init__(self, matcher=None, inspector=None, verifier=None, sam=None,
                 decider=None, requester=None, tour=None, picker=None,
                 transcript=None, noter=None, drafter=None, ruler=None,
                 opinion=None):
        self.calls = []
        self.sam_calls = []
        self.transcribe_calls = []
        self._matcher = matcher or _unknown_everything
        self._inspector = inspector or _not_shown_everything
        self._verifier = verifier or _confirm_everything
        self._sam = sam if sam is not None else (lambda call: SAM_FIXTURE)
        self._decider = decider or _decide_first
        self._requester = requester or _request_everything
        self._tour = tour or _empty_tour
        self._picker = picker or _keep_all_boxes
        self._transcript = transcript or _spanish_note
        self._noter = noter or _english_note
        self._drafter = drafter or _empty_draft
        self._ruler = ruler or _echo_rule
        self._opinion = opinion or _default_opinion
        self.trace = []
        self._hooks = []

    def add_hook(self, func):
        """Mirror MetaClient.add_hook for the per-run call log."""
        self._hooks.append(func)

    def _fire(self, purpose, model, ok=True, status=None):
        context = call_context.get() or {}
        entry = {
            "purpose": purpose,
            "model": model,
            "seconds": 0.0,
            "attempts": 1,
            "cache": "miss",
            "ok": ok,
            "usage": None,
            "photo_id": context.get("photo_id"),
        }
        if status is not None:
            entry["status"] = status
        for func in self._hooks:
            func(dict(entry))

    def respond(
        self, purpose, content, instructions=None, schema=None,
        max_output_tokens=16000, effort="medium", model=None,
    ):
        call = {
            "purpose": purpose,
            "content": content,
            "instructions": instructions,
            "schema": schema,
            "effort": effort,
            "model": model,
        }
        self.calls.append(call)
        self._fire(purpose, "test-spark")
        props = (schema or {}).get("properties", {})
        if "matches" in props:
            return self._matcher(call)
        if "verdict" in props:
            return self._verifier(call)
        if "room_id" in props:
            return self._decider(call)
        if "requests" in props:
            return self._requester(call)
        if "shots" in props:
            return self._tour(call)
        if "boxes" in props:
            return self._picker(call)
        if "translation" in props:
            return self._noter(call)
        if "unclear_rules" in props:
            return self._drafter(call)
        if "english" in props:
            return self._ruler(call)
        if "requirement_met" in props or "opinion" in props:
            return self._opinion(call)
        return self._inspector(call)

    def segment_image(self, purpose, jpeg_bytes, prompt):
        call = {"purpose": purpose, "jpeg_bytes": jpeg_bytes, "prompt": prompt}
        self.sam_calls.append(call)
        self._fire(purpose, "test-sam")
        outcome = self._sam(call)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def transcribe(self, purpose, wav_path, languages):
        call = {"purpose": purpose, "wav_path": str(wav_path),
                "languages": list(languages)}
        self.transcribe_calls.append(call)
        self._fire(purpose, "test-asr")
        outcome = self._transcript(call)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
