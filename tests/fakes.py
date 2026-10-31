"""Shared fakes for first-look tests: tiny photos and a scriptable client."""

from datetime import datetime
from pathlib import Path

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


SAM_FIXTURE = (
    Path(__file__).with_name("fixtures") / "sam_three_squares.txt"
).read_text(encoding="utf-8")


class FakeSpark:
    """Duck-typed stand-in for MetaClient.respond()/segment_image()."""

    def __init__(self, matcher=None, inspector=None, verifier=None, sam=None,
                 decider=None, requester=None, tour=None, picker=None):
        self.calls = []
        self.sam_calls = []
        self._matcher = matcher or _unknown_everything
        self._inspector = inspector or _not_shown_everything
        self._verifier = verifier or _confirm_everything
        self._sam = sam if sam is not None else (lambda call: SAM_FIXTURE)
        self._decider = decider or _decide_first
        self._requester = requester or _request_everything
        self._tour = tour or _empty_tour
        self._picker = picker or _keep_all_boxes
        self.trace = []
        self._hooks = []

    def add_hook(self, func):
        """Mirror MetaClient.add_hook for the per-run call log."""
        self._hooks.append(func)

    def _fire(self, purpose, model):
        context = call_context.get() or {}
        entry = {
            "purpose": purpose,
            "model": model,
            "seconds": 0.0,
            "attempts": 1,
            "cache": "miss",
            "ok": True,
            "usage": None,
            "photo_id": context.get("photo_id"),
        }
        for func in self._hooks:
            func(dict(entry))

    def respond(
        self, purpose, content, instructions=None, schema=None,
        max_output_tokens=16000, effort="medium",
    ):
        call = {
            "purpose": purpose,
            "content": content,
            "instructions": instructions,
            "schema": schema,
            "effort": effort,
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
        return self._inspector(call)

    def segment_image(self, purpose, jpeg_bytes, prompt):
        call = {"purpose": purpose, "jpeg_bytes": jpeg_bytes, "prompt": prompt}
        self.sam_calls.append(call)
        self._fire(purpose, "test-sam")
        outcome = self._sam(call)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
