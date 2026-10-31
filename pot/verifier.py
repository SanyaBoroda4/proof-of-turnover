"""Second look and SAM check for first-look claims.

verify() runs one Muse Spark call per done/problem claim (that call sees
only the cited photo, never the first-look claim) plus one SAM
segmentation per claim whose item has a sam_prompt. Everything runs in
parallel, at most MAX_WORKERS calls at a time. A call that fails after
all retries counts as unclear / not found, with a note; the run goes on.
"""

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

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


#: Box picks only run with this many boxes at most; more are unreadable.
MAX_PICK_BOXES = 8


def _numbered_boxes_jpeg(photo, boxes):
    """The check copy with each box drawn in red, numbered 1..N."""
    with Image.open(photo.check_path) as handle:
        handle.load()
        image = handle.convert("RGB")
    width, height = image.size
    stroke = max(3, width // 200)
    try:
        font = ImageFont.load_default(size=max(24, height // 18))
    except TypeError:  # very old Pillow without sized default fonts
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image)
    for number, box in enumerate(boxes, start=1):
        draw.rectangle(
            [box["left"], box["top"], box["right"], box["bottom"]],
            outline=(220, 38, 40), width=stroke,
        )
        label = str(number)
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        pad = stroke + 2
        badge_w = right - left + 2 * pad
        badge_h = bottom - top + 2 * pad
        badge_top = box["top"] - badge_h - stroke
        if badge_top < 0:
            badge_top = min(height - badge_h, box["top"] + stroke)
        badge_left = min(max(0, box["left"]), max(0, width - badge_w))
        draw.rectangle(
            [badge_left, badge_top, badge_left + badge_w, badge_top + badge_h],
            fill=(255, 235, 59), outline=(0, 0, 0), width=max(1, stroke // 2),
        )
        draw.text((badge_left + pad - left, badge_top + pad - top), label,
                  font=font, fill=(0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _area_box(boxes):
    """One box around the area all the boxes cover."""
    return {
        "left": min(box["left"] for box in boxes),
        "top": min(box["top"] for box in boxes),
        "right": max(box["right"] for box in boxes),
        "bottom": max(box["bottom"] for box in boxes),
        "confidence": max(box.get("confidence", 0) for box in boxes),
    }


def _pick_boxes(client, item_id, photo, sam, purpose, instructions, effort):
    """One numbered-boxes pick call; returns (SamCheck, notes)."""
    raw = base64.b64encode(_numbered_boxes_jpeg(photo, sam.boxes)).decode("ascii")
    try:
        answer = client.respond(
            purpose,
            [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{raw}"}],
            instructions=instructions,
            schema=prompts.pick_boxes_schema(),
            effort=effort,
        )
        boxes = answer.get("boxes", [])
        if not isinstance(boxes, list):
            raise ValueError(f"boxes is not a list: {boxes!r}")
        picked = []
        for number in boxes:
            if isinstance(number, bool):
                continue
            if isinstance(number, int) and 1 <= number <= sam.count and number not in picked:
                picked.append(number)
    except MetaClientError as exc:
        return sam, [f"Box pick for {item_id} failed ({exc}); kept all {sam.count}"]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return sam, [f"Box pick for {item_id} unreadable ({exc}); kept all {sam.count}"]
    kept = [sam.boxes[number - 1] for number in sorted(picked)]
    note = f"Box pick for {item_id}: kept {len(kept)} of {sam.count}"
    return SamCheck(item_id, sam.photo_id, sam.prompt, len(kept), kept), [note]


def pick_problem_boxes(client, item_id, photo, sam, problem_object, effort=None):
    """Keep only the problem boxes Muse Spark confirms; returns (SamCheck, notes).

    With one box there is nothing to choose, so no call is made. With
    several, the model sees them numbered and picks the ones showing what
    is wrong; "none" keeps no box (the caption still explains).
    """
    effort = prompts.verify_effort() if effort is None else effort
    if sam.count <= 1:
        return sam, []
    return _pick_boxes(
        client, item_id, photo, sam,
        f"pick problem boxes {item_id} {photo.id} ({problem_object})",
        prompts.pick_boxes_instructions(problem_object, sam.count),
        effort,
    )


def pick_problem_area(client, item, photo, sam, effort=None):
    """Keep the boxes outlining the item's problem_area; returns (SamCheck, notes)."""
    effort = prompts.verify_effort() if effort is None else effort
    if sam.count <= 1:
        return sam, []
    return _pick_boxes(
        client, item.id, photo, sam,
        f"pick problem area {item.id} {photo.id}",
        prompts.pick_area_instructions(item.problem_area, sam.count, item.requirement),
        effort,
    )


def refine_problem_boxes(client, item_id, photo, sam, problem_object, effort=None):
    """Narrow a problem SAM result; returns (SamCheck, notes).

    One box is kept as is. Two to eight boxes go through one Muse
    Spark pick call. More than eight would be unreadable when
    numbered, so no call is made and one red box covers their whole
    area instead.
    """
    if sam.count <= 1:
        return sam, []
    if sam.count > MAX_PICK_BOXES:
        area = _area_box(sam.boxes)
        note = (f"Box pick for {item_id} skipped "
                f"({sam.count} boxes): drew 1 area box")
        return SamCheck(item_id, sam.photo_id, sam.prompt, 1, [area]), [note]
    return pick_problem_boxes(client, item_id, photo, sam, problem_object, effort)


def refine_problem_area(client, item, photo, sam, problem_object, effort=None):
    """Refine an area-prompted problem SAM; returns (SamCheck, notes).

    sam is the raw result of SAM on item.problem_area. One box is
    kept; 2-8 boxes go through one area pick call; more than 8 become
    one area box with no call. Zero boxes fall back to SAM on
    problem_object with today's refinement (empty when that is "").
    Both the live and the batch path share this helper.
    """
    effort = prompts.verify_effort() if effort is None else effort
    if sam.count == 1:
        return sam, []
    if 2 <= sam.count <= MAX_PICK_BOXES:
        return pick_problem_area(client, item, photo, sam, effort)
    if sam.count > MAX_PICK_BOXES:
        area = _area_box(sam.boxes)
        note = (f"Box pick for {item.id} skipped "
                f"({sam.count} boxes): drew 1 area box")
        return SamCheck(item.id, sam.photo_id, sam.prompt, 1, [area]), [note]
    if not problem_object:
        return sam, []
    obj, notes = check_sam(
        client, item.id, photo, problem_object,
        f"sam problem {item.id} {photo.id} ({problem_object})",
    )
    notes.insert(0, f"Area '{item.problem_area}' found nothing for {item.id}; "
                    f"tried '{problem_object}'")
    refined, refine_notes = refine_problem_boxes(
        client, item.id, photo, obj, problem_object, effort
    )
    return refined, notes + refine_notes


def segment_problem_objects(client, proofs, seconds, photos_by_id, effort=None,
                            items_by_id=None):
    """SAM-segment each final problem's problem_object; returns {item_id: SamCheck}.

    Items with a problem_area are segmented on the area instead (even
    when the second look named no object) and refined with the shared
    area helper. Multi-box results go through one Muse Spark pick call
    per item.
    """
    effort = prompts.verify_effort() if effort is None else effort
    items_by_id = items_by_id or {}
    jobs = []
    for item_id in sorted(proofs):
        if proofs[item_id].status != PROBLEM:
            continue
        second = seconds.get(item_id)
        photo = photos_by_id.get(proofs[item_id].photo_id)
        item = items_by_id.get(item_id)
        area = item.problem_area if item is not None else None
        if second is None or photo is None:
            continue
        if not second.problem_object and not area:
            continue
        if area:
            prompt = area
            purpose = f"sam problem area {item_id} {photo.id} ({area})"
        else:
            prompt = second.problem_object
            purpose = f"sam problem {item_id} {photo.id} ({prompt})"
        jobs.append((item_id, item, photo, prompt, purpose))
    found = {}
    notes = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            (item_id, submit_with_context(
                pool, check_sam, client, item_id, photo, prompt, purpose,
            ))
            for item_id, item, photo, prompt, purpose in jobs
        ]
        sams = {}
        for item_id, future in sorted(futures):
            result, job_notes = future.result()
            notes.extend(job_notes)
            sams[item_id] = result
        picks = []
        for item_id in sorted(sams):
            item = items_by_id.get(item_id)
            photo = photos_by_id[proofs[item_id].photo_id]
            if item is not None and item.problem_area:
                func = refine_problem_area
                args = (client, item, photo, sams[item_id],
                        seconds[item_id].problem_object, effort)
            elif sams[item_id].count > 1:
                func = refine_problem_boxes
                args = (client, item_id, photo, sams[item_id],
                        seconds[item_id].problem_object, effort)
            else:
                continue
            picks.append((item_id, submit_with_context(pool, func, *args)))
        for item_id, future in picks:
            result, job_notes = future.result()
            notes.extend(job_notes)
            sams[item_id] = result
        found.update(sams)
    return found, notes
