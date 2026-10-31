"""Match every photo to a room and decide room coverage by code.

match_rooms() asks Muse Spark which room each photo shows, in batches of
at most MATCH_BATCH_SIZE images. A room with no photo matched at high or
medium confidence is "not photographed". Coverage is decided here by code
(covered / needs proof), never by the model.
"""

from dataclasses import dataclass, field

from pot import prompts

MATCH_BATCH_SIZE = 40

CONFIDENCES = ("high", "medium", "low")
COVERED_CONFIDENCES = ("high", "medium")


@dataclass
class Assignment:
    photo_id: str
    room_id: str  # a room id, or "unknown"
    confidence: str  # high / medium / low
    seen: str


@dataclass
class MatchResult:
    assignments: dict  # photo_id -> Assignment
    coverage: dict  # room_id -> "covered" | "needs proof"
    notes: list = field(default_factory=list)


def match_rooms_schema(photo_ids, room_ids):
    return {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "photo_id": {"type": "string", "enum": list(photo_ids)},
                        "room_id": {"type": "string", "enum": list(room_ids) + ["unknown"]},
                        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                        "seen": {"type": "string"},
                    },
                    "required": ["photo_id", "room_id", "confidence", "seen"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }


def _unknown(photo_id, seen=""):
    return Assignment(photo_id=photo_id, room_id="unknown", confidence="low", seen=seen)


def _check_batch_answer(answer, batch_ids, room_ids, notes):
    """Validate one batch answer; bad entries become unknown, never crash."""
    assignments = {}
    if not isinstance(answer, dict) or not isinstance(answer.get("matches"), list):
        notes.append(f"Match batch {batch_ids[0]}-{batch_ids[-1]}: bad answer shape, all unknown")
        return {photo_id: _unknown(photo_id) for photo_id in batch_ids}
    valid_rooms = set(room_ids) | {"unknown"}
    batch_set = set(batch_ids)
    for entry in answer["matches"]:
        if not isinstance(entry, dict):
            notes.append(f"Match batch {batch_ids[0]}-{batch_ids[-1]}: skipped a malformed entry")
            continue
        photo_id = entry.get("photo_id")
        if photo_id not in batch_set:
            notes.append(f"Match batch {batch_ids[0]}-{batch_ids[-1]}: unknown photo id {photo_id!r}")
            continue
        if photo_id in assignments:
            notes.append(f"Match: duplicate entry for {photo_id}, kept the first")
            continue
        room_id = entry.get("room_id")
        if room_id not in valid_rooms:
            notes.append(f"Match: {photo_id} has invalid room id {room_id!r}, set to unknown")
            assignments[photo_id] = _unknown(photo_id)
            continue
        confidence = entry.get("confidence")
        if confidence not in CONFIDENCES:
            notes.append(f"Match: {photo_id} has invalid confidence {confidence!r}, set to low")
            confidence = "low"
        seen = entry.get("seen") or ""
        assignments[photo_id] = Assignment(
            photo_id=photo_id, room_id=room_id, confidence=confidence, seen=seen
        )
    for photo_id in batch_ids:
        if photo_id not in assignments:
            notes.append(f"Match: no entry returned for {photo_id}, set to unknown")
            assignments[photo_id] = _unknown(photo_id)
    return assignments


def match_rooms(client, photos, property):
    """Match each photo to a room; returns a MatchResult."""
    rooms = property["rooms"]
    room_ids = [room["id"] for room in rooms]
    notes = []
    assignments = {}
    if not photos:
        notes.append("Match: no photos to match")
    batches = [photos[i : i + MATCH_BATCH_SIZE] for i in range(0, len(photos), MATCH_BATCH_SIZE)]
    for number, batch in enumerate(batches, start=1):
        batch_ids = [photo.id for photo in batch]
        content = []
        for photo in batch:
            content.extend(prompts.photo_parts(photo))
        purpose = f"match rooms batch {number}/{len(batches)} ({batch_ids[0]}-{batch_ids[-1]})"
        answer = client.respond(
            purpose,
            content,
            instructions=prompts.match_rooms_instructions(rooms),
            schema=match_rooms_schema(batch_ids, room_ids),
            effort=prompts.REASONING_EFFORT,
        )
        assignments.update(_check_batch_answer(answer, batch_ids, room_ids, notes))
    coverage = {}
    for room_id in room_ids:
        covered = any(
            a.room_id == room_id and a.confidence in COVERED_CONFIDENCES
            for a in assignments.values()
        )
        coverage[room_id] = "covered" if covered else "needs proof"
    return MatchResult(assignments=assignments, coverage=coverage, notes=notes)
