"""First look: one Muse Spark claim per checklist item, per room.

first_look() sends one call per room (in parallel, at most MAX_WORKERS at
a time), each with that room's photos plus the "unknown" ones and only
that room's checklist items. These are claims ("done" / "problem" /
"not_shown"), never proof: the second look, SAM and the proof rule come
later. Coverage items are never sent to the model.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pot import prompts
from pot.meta_client import MetaClientError
from pot.photos import normalize_photo_id

ROOM_MAX_IMAGES = 40
MAX_WORKERS = 6

STATUSES = ("done", "problem", "not_shown")


@dataclass
class Claim:
    item_id: str
    status: str  # done / problem / not_shown
    photo_id: str  # "" when not_shown
    seen: str
    count_seen: int  # int, or None


@dataclass
class OtherIssue:
    photo_id: str
    description: str
    room_id: str


@dataclass
class FirstLook:
    claims: dict  # item_id -> Claim
    other_issues: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def first_look_schema(item_ids, photo_ids):
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "enum": list(item_ids)},
                        "status": {"type": "string", "enum": list(STATUSES)},
                        "photo_id": {"type": "string", "enum": list(photo_ids) + [""]},
                        "seen": {"type": "string"},
                        "count_seen": {"type": ["integer", "null"]},
                    },
                    "required": ["item_id", "status", "photo_id", "seen", "count_seen"],
                    "additionalProperties": False,
                },
            },
            "other_issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "photo_id": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["photo_id", "description"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims", "other_issues"],
        "additionalProperties": False,
    }


def _not_shown(item_id):
    return Claim(item_id=item_id, status="not_shown", photo_id="", seen="", count_seen=None)


def _check_count(entry, item, notes):
    if item.min_count is None:
        return None
    count = entry.get("count_seen")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        notes.append(f"First look: {item.id} has invalid count_seen {count!r}, set to null")
        return None
    return count


def _check_room_answer(answer, items, photo_ids, room_label, notes, unknown_ids=()):
    """Validate one room answer; anything bad becomes not_shown, never crash."""
    claims = {}
    issues = []
    by_id = {item.id: item for item in items}
    photo_set = set(photo_ids)
    if (
        not isinstance(answer, dict)
        or not isinstance(answer.get("claims"), list)
        or not isinstance(answer.get("other_issues"), list)
    ):
        notes.append(f"First look {room_label}: bad answer shape, all items not_shown")
        return {item.id: _not_shown(item.id) for item in items}, []
    for entry in answer["claims"]:
        if not isinstance(entry, dict):
            notes.append(f"First look {room_label}: skipped a malformed claim")
            continue
        item_id = entry.get("item_id")
        if item_id not in by_id:
            notes.append(f"First look {room_label}: unknown item id {item_id!r}")
            continue
        if item_id in claims:
            notes.append(f"First look: duplicate claim for {item_id}, kept the first")
            continue
        item = by_id[item_id]
        status = entry.get("status")
        photo_id = normalize_photo_id(entry.get("photo_id"))
        seen = entry.get("seen") or ""
        if status not in STATUSES:
            notes.append(f"First look: {item_id} has invalid status {status!r}, set to not_shown")
            status = "not_shown"
        if status in ("done", "problem"):
            if photo_id not in photo_set:
                notes.append(
                    f"First look: {item_id} is {status} without a valid photo, set to not_shown"
                )
                claims[item_id] = _not_shown(item_id)
                continue
            if photo_id in unknown_ids:
                notes.append(
                    f"First look: {item_id} cites {photo_id}, matched to no room; "
                    "check it shows this room"
                )
        else:
            photo_id = ""
        claims[item_id] = Claim(
            item_id=item_id,
            status=status,
            photo_id=photo_id,
            seen=seen,
            count_seen=_check_count(entry, item, notes) if status != "not_shown" else None,
        )
    for item in items:
        if item.id not in claims:
            notes.append(f"First look: no claim returned for {item.id}, set to not_shown")
            claims[item.id] = _not_shown(item.id)
    for entry in answer["other_issues"]:
        if not isinstance(entry, dict):
            notes.append(f"First look {room_label}: skipped a malformed other_issue")
            continue
        photo_id = normalize_photo_id(entry.get("photo_id"))
        description = entry.get("description") or ""
        if photo_id not in photo_set or not description:
            notes.append(f"First look {room_label}: dropped an invalid other_issue")
            continue
        issues.append(OtherIssue(photo_id=photo_id, description=description, room_id=room_label))
    return claims, issues


def _room_call_photos(room_photos, unknown_photos, room_label, notes):
    chosen = list(room_photos) + list(unknown_photos)
    if len(chosen) > ROOM_MAX_IMAGES:
        notes.append(
            f"First look {room_label}: {len(chosen)} photos available, sent the first "
            f"{ROOM_MAX_IMAGES} (room photos first)"
        )
        chosen = chosen[:ROOM_MAX_IMAGES]
    return chosen


def _room_call(client, room_id, room_name, room_description, items, mine, others):
    notes = []
    chosen = _room_call_photos(mine, others, room_id, notes)
    content = []
    for photo in chosen:
        content.extend(prompts.photo_parts(photo, prompts.FIRST_LOOK_PHOTO_SIZE))
    photo_ids = [photo.id for photo in chosen]
    label = room_id if room_name is None else f"{room_id} ({room_name})"
    unknown_ids = {photo.id for photo in chosen} & {photo.id for photo in others}
    try:
        answer = client.respond(
            f"first look {room_id} ({','.join(photo_ids)})",
            content,
            instructions=prompts.first_look_instructions(room_name, room_description, items),
            schema=first_look_schema([item.id for item in items], photo_ids),
            effort=prompts.REASONING_EFFORT,
        )
    except MetaClientError as exc:
        notes.append(f"First look {room_id}: check failed: {exc}")
        return {item.id: _not_shown(item.id) for item in items}, [], notes
    claims, issues = _check_room_answer(answer, items, photo_ids, room_id, notes, unknown_ids)
    return claims, issues, notes


def _house_calls(client, items, photos):
    """Batched calls for items with no room; keep the first non-not_shown answer."""
    notes = []
    claims = {}
    issues = []
    pending = list(items)
    batches = [photos[i : i + ROOM_MAX_IMAGES] for i in range(0, len(photos), ROOM_MAX_IMAGES)]
    for number, batch in enumerate(batches, start=1):
        if not pending:
            break
        content = []
        for photo in batch:
            content.extend(prompts.photo_parts(photo, prompts.FIRST_LOOK_PHOTO_SIZE))
        photo_ids = [photo.id for photo in batch]
        try:
            answer = client.respond(
                f"first look house batch {number}/{len(batches)}",
                content,
                instructions=prompts.first_look_instructions(None, None, pending),
                schema=first_look_schema([item.id for item in pending], photo_ids),
                effort=prompts.REASONING_EFFORT,
            )
        except MetaClientError as exc:
            notes.append(f"First look house batch {number}/{len(batches)}: check failed: {exc}")
            break
        batch_claims, batch_issues = _check_room_answer(answer, pending, photo_ids, "house", notes)
        issues.extend(batch_issues)
        still_pending = []
        for item in pending:
            claim = batch_claims[item.id]
            if claim.status == "not_shown":
                still_pending.append(item)
            else:
                claims[item.id] = claim
        pending = still_pending
    for item in pending:
        claims[item.id] = _not_shown(item.id)
    return claims, issues, notes


def first_look(client, photos, assignments, checklist, rooms=None):
    """Claim every non-coverage checklist item; returns a FirstLook."""
    rooms = rooms or {}
    room_photos = {}
    unknown = []
    for photo in photos:
        assignment = assignments.get(photo.id)
        if assignment is None or assignment.room_id == "unknown":
            unknown.append(photo)
        else:
            room_photos.setdefault(assignment.room_id, []).append(photo)

    room_items = {}
    house_items = []
    for item in checklist:
        if item.key == "coverage":
            continue
        if item.room_id == "house":
            house_items.append(item)
        else:
            room_items.setdefault(item.room_id, []).append(item)

    tasks = []
    for room_id in sorted(room_items):
        items = room_items[room_id]
        mine = list(room_photos.get(room_id, []))
        others = [p for p in unknown if p not in mine]
        if not mine and not others:
            continue
        room = rooms.get(room_id, {})
        name = room.get("name") or items[0].room_name
        tasks.append((room_id, name, room.get("description"), items, mine, others))
    if house_items and photos:
        tasks.append(("house",))

    claims = {}
    issues = []
    notes = []
    for room_id, items in sorted(room_items.items()):
        if not room_photos.get(room_id) and not unknown:
            notes.append(f"First look {room_id}: no photos, all items not_shown")
            for item in items:
                claims[item.id] = _not_shown(item.id)
    if house_items and not photos:
        notes.append("First look house: no photos, all items not_shown")
        for item in house_items:
            claims[item.id] = _not_shown(item.id)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for task in tasks:
            room_id = task[0]
            if room_id == "house":
                future = pool.submit(_house_calls, client, house_items, list(photos))
            else:
                _, name, description, items, mine, others = task
                future = pool.submit(
                    _room_call, client, room_id, name, description, items, mine, others
                )
            futures[room_id] = future
        kept_unknown_issue = {}
        for room_id in sorted(futures):
            room_claims, room_issues, room_notes = futures[room_id].result()
            claims.update(room_claims)
            for issue in room_issues:
                assignment = assignments.get(issue.photo_id)
                if assignment is not None and assignment.room_id == "unknown":
                    if issue.photo_id in kept_unknown_issue:
                        notes.append(
                            f"First look: dropped duplicate issue for unknown photo "
                            f"{issue.photo_id} from {room_id} "
                            f"(kept {kept_unknown_issue[issue.photo_id]}'s)"
                        )
                        continue
                    kept_unknown_issue[issue.photo_id] = room_id
                issues.append(issue)
            notes.extend(room_notes)
    return FirstLook(claims=claims, other_issues=issues, notes=notes)
