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
from pot.meta_client import MetaClientError, submit_with_context
from pot.proof import PROBLEM
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
    problem_object: str = ""  # 1-3 words for what is wrong, or ""


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
    room_decisions: dict = field(default_factory=dict)  # photo_id -> room_id | "none"
    notes: list = field(default_factory=list)


def second_look_schema():
    return {
        "type": "object",
        "properties": {
            "this_room": {"type": "string", "enum": list(THIS_ROOM_VALUES)},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "count_seen": {"type": ["integer", "null"]},
            "reason": {"type": "string"},
            "problem_object": {"type": "string"},
        },
        "required": ["this_room", "verdict", "count_seen", "reason", "problem_object"],
        "additionalProperties": False,
    }


def room_decision_schema(room_ids):
    return {
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "enum": list(room_ids) + ["none"]},
        },
        "required": ["room_id"],
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
    problem_object = answer.get("problem_object")
    if not isinstance(problem_object, str):
        problem_object = ""
    return SecondLook(
        item_id=item.id,
        photo_id=photo_id,
        this_room=answer["this_room"],
        verdict=answer["verdict"],
        count_seen=count,
        reason=answer.get("reason") or "",
        problem_object=problem_object,
    )


def check_second_look(client, item, photo, room_name, room_description, effort):
    """One second-look call; failures become unclear, never raise."""
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
            effort=effort,
        )
    except MetaClientError as exc:
        notes.append(f"Second look {item.id}: check failed: {exc}")
        return _unclear(item.id, photo.id), notes
    return _check_second_answer(answer, item, photo.id, notes), notes


def check_sam(client, item_id, photo, prompt, purpose):
    """One SAM segmentation; failures count as not found, never raise."""
    notes = []
    try:
        output_text = client.segment_image(
            purpose,
            photo.check_path.read_bytes(),
            prompt,
        )
    except MetaClientError as exc:
        notes.append(f"SAM {item_id}: check failed: {exc}")
        return SamCheck(item_id, photo.id, prompt, 0, []), notes
    try:
        boxes = detect(output_text, min_confidence=SAM_MIN_CONFIDENCE)
    except Exception as exc:
        notes.append(f"SAM {item_id}: output unreadable ({exc}), counted as none")
        return SamCheck(item_id, photo.id, prompt, 0, []), notes
    return SamCheck(item_id, photo.id, prompt, len(boxes), boxes), notes


def _room_decision_call(client, photo, room_ids, rooms, effort):
    notes = []
    content = prompts.photo_parts(photo, prompts.VERIFY_PHOTO_SIZE)
    candidates = [rooms[room_id] for room_id in room_ids if room_id in rooms]
    try:
        answer = client.respond(
            f"room decision {photo.id} ({','.join(room_ids)})",
            content,
            instructions=prompts.room_decision_instructions(candidates),
            schema=room_decision_schema(room_ids),
            effort=effort,
        )
    except MetaClientError as exc:
        notes.append(f"Room decision {photo.id}: check failed: {exc}")
        return "none", notes
    room_id = answer.get("room_id") if isinstance(answer, dict) else None
    if room_id not in list(room_ids) + ["none"]:
        notes.append(f"Room decision {photo.id}: bad answer {room_id!r}, treated as none")
        return "none", notes
    return room_id, notes


def verify(client, checklist, claims, photos, rooms=None, assignments=None, effort=None):
    """Second-look + SAM + room-decision checks; returns a VerifyResult."""
    rooms = rooms or {}
    assignments = assignments or {}
    effort = prompts.verify_effort() if effort is None else effort
    by_id = {item.id: item for item in checklist}
    photos_by_id = {photo.id: photo for photo in photos}
    jobs = []
    cited_unknown = {}
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
            ("look", item.id, (check_second_look, (client, item, photo,
                                                  room.get("name") or item.room_name,
                                                  room.get("description"), effort)))
        )
        if item.sam_prompt:
            jobs.append(
                ("sam", item.id, (check_sam, (client, item.id, photo, item.sam_prompt,
                                              f"sam {item.id} {photo.id} ({item.sam_prompt})")))
            )
        assignment = assignments.get(claim.photo_id)
        if assignment is None or assignment.room_id == "unknown":
            cited_unknown.setdefault(claim.photo_id, set()).add(item.room_id)
    for photo_id in sorted(cited_unknown):
        photo = photos_by_id.get(photo_id)
        if photo is None:
            continue
        room_ids = sorted(cited_unknown[photo_id])
        jobs.append(
            ("room", photo_id, (_room_decision_call, (client, photo, room_ids, rooms, effort)))
        )

    seconds = {}
    sams = {}
    decisions = {}
    notes = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = []
        for kind, key, (func, args) in jobs:
            futures.append((kind, key, submit_with_context(pool, func, *args)))
        for kind, key, future in sorted(futures, key=lambda entry: (entry[0], entry[1])):
            result, job_notes = future.result()
            notes.extend(job_notes)
            if kind == "look":
                seconds[key] = result
            elif kind == "sam":
                sams[key] = result
            else:
                decisions[key] = result
    return VerifyResult(second_looks=seconds, sams=sams, room_decisions=decisions, notes=notes)


def segment_problem_objects(client, proofs, seconds, photos_by_id):
    """SAM-segment each final problem's problem_object; returns {item_id: SamCheck}."""
    jobs = []
    for item_id in sorted(proofs):
        if proofs[item_id].status != PROBLEM:
            continue
        second = seconds.get(item_id)
        photo = photos_by_id.get(proofs[item_id].photo_id)
        if second is None or not second.problem_object or photo is None:
            continue
        jobs.append((item_id, photo, second.problem_object))
    found = {}
    notes = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            (item_id, submit_with_context(
                pool, check_sam, client, item_id, photo, prompt,
                f"sam problem {item_id} {photo.id} ({prompt})",
            ))
            for item_id, photo, prompt in jobs
        ]
        for item_id, future in sorted(futures):
            result, job_notes = future.result()
            notes.extend(job_notes)
            found[item_id] = result
    return found, notes
