"""Second look and SAM check for first-look claims.

verify() runs one Muse Spark call per done/problem claim (that call sees
only the cited photo, never the first-look claim) plus one SAM
segmentation per claim whose item has a sam_prompt. Everything runs in
parallel, at most MAX_WORKERS calls at a time. A call that fails after
all retries counts as unclear / not found, with a note; the run goes on.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pot import prompts
from pot.meta_client import MetaClientError
from pot.sam import detect

MAX_WORKERS = 6
SAM_MIN_CONFIDENCE = 0.5

THIS_ROOM_VALUES = ("yes", "no", "unclear")
VERDICTS = ("done", "not_done", "unclear")


@dataclass
class SecondLook:
    item_id: str
    photo_id: str
    this_room: str  # yes / no / unclear
    verdict: str  # done / not_done / unclear
    count_seen: int  # int, or None
    reason: str


@dataclass
class SamCheck:
    item_id: str
    photo_id: str
    prompt: str
    count: int
    boxes: list  # pot.sam.detect boxes


@dataclass
class VerifyResult:
    second_looks: dict  # item_id -> SecondLook
    sams: dict  # item_id -> SamCheck
    notes: list = field(default_factory=list)


def second_look_schema():
    return {
        "type": "object",
        "properties": {
            "this_room": {"type": "string", "enum": list(THIS_ROOM_VALUES)},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "count_seen": {"type": ["integer", "null"]},
            "reason": {"type": "string"},
        },
        "required": ["this_room", "verdict", "count_seen", "reason"],
        "additionalProperties": False,
    }


def _unclear(item_id, photo_id):
    return SecondLook(
        item_id=item_id, photo_id=photo_id, this_room="unclear",
        verdict="unclear", count_seen=None, reason="",
    )


def _check_second_answer(answer, item, photo_id, notes):
    """Validate one second-look answer; bad answers become unclear."""
    if (
        not isinstance(answer, dict)
        or answer.get("this_room") not in THIS_ROOM_VALUES
        or answer.get("verdict") not in VERDICTS
    ):
        notes.append(f"Second look {item.id}: bad answer, treated as unclear")
        return _unclear(item.id, photo_id)
    count = answer.get("count_seen")
    if item.min_count is None or count is None:
        count = None
    elif isinstance(count, bool) or not isinstance(count, int) or count < 0:
        notes.append(f"Second look {item.id}: invalid count_seen {count!r}, set to null")
        count = None
    return SecondLook(
        item_id=item.id,
        photo_id=photo_id,
        this_room=answer["this_room"],
        verdict=answer["verdict"],
        count_seen=count,
        reason=answer.get("reason") or "",
    )


def _second_look_call(client, item, photo, room_name, room_description):
    notes = []
    content = prompts.photo_parts(photo, prompts.VERIFY_PHOTO_SIZE)
    try:
        answer = client.respond(
            f"second look {item.id} {photo.id}",
            content,
            instructions=prompts.second_look_instructions(
                room_name, room_description, item.requirement, item.min_count
            ),
            schema=second_look_schema(),
            effort=prompts.VERIFY_EFFORT,
        )
    except MetaClientError as exc:
        notes.append(f"Second look {item.id}: check failed: {exc}")
        return _unclear(item.id, photo.id), notes
    return _check_second_answer(answer, item, photo.id, notes), notes


def _sam_call(client, item, photo):
    notes = []
    try:
        output_text = client.segment_image(
            f"sam {item.id} {photo.id} ({item.sam_prompt})",
            photo.check_path.read_bytes(),
            item.sam_prompt,
        )
    except MetaClientError as exc:
        notes.append(f"SAM {item.id}: check failed: {exc}")
        return SamCheck(item.id, photo.id, item.sam_prompt, 0, []), notes
    try:
        boxes = detect(output_text, min_confidence=SAM_MIN_CONFIDENCE)
    except Exception as exc:
        notes.append(f"SAM {item.id}: output unreadable ({exc}), counted as none")
        return SamCheck(item.id, photo.id, item.sam_prompt, 0, []), notes
    return SamCheck(item.id, photo.id, item.sam_prompt, len(boxes), boxes), notes


def verify(client, checklist, claims, photos, rooms=None):
    """Second-look + SAM checks for done/problem claims; returns VerifyResult."""
    rooms = rooms or {}
    by_id = {item.id: item for item in checklist}
    photos_by_id = {photo.id: photo for photo in photos}
    jobs = []
    for item_id in sorted(claims):
        claim = claims[item_id]
        if claim.status not in ("done", "problem") or not claim.photo_id:
            continue
        item = by_id.get(item_id)
        photo = photos_by_id.get(claim.photo_id)
        if item is None or photo is None:
            continue
        room = rooms.get(item.room_id, {})
        jobs.append(
            ("look", item, photo, room.get("name") or item.room_name,
             room.get("description"))
        )
        if item.sam_prompt:
            jobs.append(("sam", item, photo, None, None))

    seconds = {}
    sams = {}
    notes = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = []
        for kind, item, photo, name, description in jobs:
            if kind == "look":
                future = pool.submit(_second_look_call, client, item, photo, name, description)
            else:
                future = pool.submit(_sam_call, client, item, photo)
            futures.append((kind, item.id, future))
        for kind, item_id, future in sorted(futures):
            result, job_notes = future.result()
            notes.extend(job_notes)
            if kind == "look":
                seconds[item_id] = result
            else:
                sams[item_id] = result
    return VerifyResult(second_looks=seconds, sams=sams, notes=notes)
