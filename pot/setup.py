"""Host house setup: answers + room photos + rules -> property.json + tour.

A setup lives in data/setups/<setup_id>/: setup.json plus a photos/
folder. The host answers a few questions (rooms are built in code, no
model call), photographs each clean room, types or speaks rules, and
one Muse Spark call per room drafts the checklist from rules + photos.
Saving writes properties/<id>/property.json and plans the photo tour.
All model calls go through pot/meta_client.py; the server routes them
into the setup's calls.jsonl with the call_context mechanism.
"""

import json
import os
import re
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from pot import config, examples as examples_mod, prompts
from pot.checklist import (
    PropertyError,
    build_checklist,
    load_property,
    validate_property,
)
from pot.notes import MAX_NOTE_BYTES, convert_to_wav, transcript_text
from pot.photos import ingest_file
from pot.rooms import ROOM_TYPES, items_for_room
from pot.tour import generate_tour

SETUP_ID_PATTERN = re.compile(r"^setup-(\d{8})-(\d{6})$")

#: At most 3 photos per setup room; spoken rules capped like voice notes.
MAX_ROOM_PHOTOS = 3
MAX_RULE_SECONDS = 75


class SetupError(ValueError):
    """A setup request was rejected; the message is safe for a 400."""


class SetupNotFound(SetupError):
    """Unknown setup, room or photo; the server answers 404."""


class SetupBusy(SetupError):
    """A save or tour retry is already running; the server answers 409."""


_locks = {}
_locks_guard = threading.Lock()

#: Setup ids with a save or tour retry in flight (memory only, so a
#: crash can't leave them stuck).
_saving = set()
_saving_guard = threading.Lock()


def _claim_saving(setup_id):
    with _saving_guard:
        if setup_id in _saving:
            return False
        _saving.add(setup_id)
        return True


def _release_saving(setup_id):
    with _saving_guard:
        _saving.discard(setup_id)


def _lock_for(setup_id):
    # RLock: public functions take it and call load_setup, which takes it too.
    with _locks_guard:
        return _locks.setdefault(setup_id, threading.RLock())


def slugify(text, max_len=40):
    """Lowercase a-z/0-9/- slug, up to max_len chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-")


def new_setup_id():
    return "setup-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _setup_dir(setup_id):
    if not SETUP_ID_PATTERN.match(setup_id or ""):
        raise SetupError(f"Bad setup id {setup_id!r}")
    return config.SETUPS_DIR / setup_id


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _check_int(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, int):
        raise SetupError(f"Field '{field}' must be an integer")
    if not low <= value <= high:
        raise SetupError(f"Field '{field}' must be {low}-{high}")
    return value


def _room_entry(room_id, room_type, name, floor):
    return {
        "id": room_id,
        "type": room_type,
        "name": name,
        "floor": floor,
        "rules": "",
        "photos": [],
        "draft": None,
        "status": "new",  # new | ready | skipped
        "stale": False,
        "next_photo": 1,
    }


def build_room_list(floors, bedrooms, full_baths, half_baths, kitchen,
                    living_room, dining_room, laundry, back_porch, front_porch):
    """The setup's rooms in house order (no model call)."""
    groups = [
        ("Front porch", "outdoor", 1 if front_porch else 0),
        ("Kitchen", "kitchen", 1 if kitchen else 0),
        ("Living room", "living_room", 1 if living_room else 0),
        ("Dining room", "dining_room", 1 if dining_room else 0),
        ("Half bathroom", "half_bathroom", half_baths),
        ("Laundry", "laundry", 1 if laundry else 0),
        ("Back porch", "outdoor", 1 if back_porch else 0),
        ("Bedroom", "bedroom", bedrooms),
        ("Bathroom", "bathroom", full_baths),
    ]
    rooms = []
    for name, room_type, count in groups:
        for number in range(1, count + 1):
            full_name = f"{name} {number}" if count >= 2 else name
            floor = 1
            if floors >= 2 and room_type in ("bedroom", "bathroom"):
                floor = 2
            rooms.append(_room_entry(slugify(full_name), room_type, full_name, floor))
    return rooms


def create_setup(name, floors, bedrooms, full_baths, half_baths, kitchen,
                 living_room, dining_room, laundry, back_porch, front_porch,
                 language):
    """Start a setup from the host's answers; returns the setup record."""
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise SetupError("House name must be 1-80 characters")
    _check_int(floors, "floors", 1, 4)
    _check_int(bedrooms, "bedrooms", 0, 8)
    _check_int(full_baths, "full_baths", 0, 6)
    _check_int(half_baths, "half_baths", 0, 4)
    flags = {
        "kitchen": kitchen, "living_room": living_room, "dining_room": dining_room,
        "laundry": laundry, "back_porch": back_porch, "front_porch": front_porch,
    }
    for field, value in flags.items():
        if not isinstance(value, bool):
            raise SetupError(f"Field '{field}' must be true or false")
    if language not in config.CLEANER_LANGUAGE_CODES:
        raise SetupError(f"Unsupported language {language!r}")
    rooms = build_room_list(
        floors, bedrooms, full_baths, half_baths, kitchen, living_room,
        dining_room, laundry, back_porch, front_porch,
    )
    if not rooms:
        raise SetupError("Pick at least one room")
    setup = {
        "id": None,
        "name": name,
        "floors": floors,
        "language": language,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rooms": rooms,
        "property_id": None,
    }
    for _attempt in range(5):
        setup_id = new_setup_id()
        setup_dir = _setup_dir(setup_id)
        if not setup_dir.exists():
            break
        time.sleep(1.1)
    else:
        raise SetupError("Could not start a setup, try again")
    setup["id"] = setup_id
    with _lock_for(setup_id):
        _atomic_write(setup_dir / "setup.json", setup)
    return setup


def load_setup(setup_id):
    """Read a setup (raises SetupNotFound when unknown)."""
    with _lock_for(setup_id):
        path = _setup_dir(setup_id) / "setup.json"
        if not path.is_file():
            raise SetupNotFound(f"Unknown setup '{setup_id}'")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SetupError(f"Setup '{setup_id}' is unreadable: {exc}") from exc
        if not isinstance(data, dict) or data.get("id") != setup_id:
            raise SetupError(f"Setup '{setup_id}' is unreadable")
        for room in data.get("rooms", []):
            room.setdefault("stale", False)
            if "next_photo" not in room:
                room["next_photo"] = max(room.get("photos", []) + [0]) + 1
        return data


def _save_locked(setup):
    _atomic_write(_setup_dir(setup["id"]) / "setup.json", setup)


def _room_locked(setup, room_id):
    for room in setup["rooms"]:
        if room["id"] == room_id:
            return room
    raise SetupNotFound(f"Unknown room '{room_id}'")


def update_room(setup_id, room_id, edits):
    """Save host edits; returns the updated room."""
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        room = _room_locked(setup, room_id)
        if "name" in edits:
            name = (edits["name"] or "").strip()
            if not name or len(name) > 80:
                raise SetupError("Room name must be 1-80 characters")
            room["name"] = name
        if "floor" in edits:
            floor = edits["floor"]
            if isinstance(floor, bool) or not isinstance(floor, int):
                raise SetupError("Room floor must be an integer")
            room["floor"] = floor
        if "rules" in edits:
            rules = edits["rules"]
            if not isinstance(rules, str) or len(rules) > 2000:
                raise SetupError("Rules must be text of up to 2000 characters")
            if room["draft"] is not None and rules != room["rules"]:
                room["stale"] = True
            room["rules"] = rules
        if "skipped" in edits:
            if not isinstance(edits["skipped"], bool):
                raise SetupError("Field 'skipped' must be true or false")
            room["status"] = "skipped" if edits["skipped"] else (
                "ready" if room["draft"] else "new"
            )
        if any(key in edits for key in ("beds", "towels", "items")):
            if room["draft"] is None:
                raise SetupError("Make the checklist before editing it")
            if "beds" in edits:
                beds = edits["beds"]
                if not isinstance(beds, list):
                    raise SetupError("Beds must be a list of bed names")
                beds = [b.strip() for b in beds
                        if isinstance(b, str) and b.strip()][:8]
                room["draft"]["beds"] = beds or ["bed"]
            if "towels" in edits:
                towels = edits["towels"]
                if isinstance(towels, bool) or not isinstance(towels, int):
                    raise SetupError("Towels must be an integer")
                room["draft"]["towels"] = towels
            if "items" in edits:
                room["draft"]["items"] = _fix_edited_items(edits["items"])
        _save_locked(setup)
        return room


def _fix_edited_items(items):
    if not isinstance(items, list):
        raise SetupError("Items must be a list")
    fixed = []
    seen = set()
    for entry in items:
        if not isinstance(entry, dict):
            raise SetupError("Each item must be an object")
        key = _fix_key(entry.get("key"), seen, entry.get("label") or "item")
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise SetupError("Each item needs a label")
        requirement = entry.get("requirement")
        if not isinstance(requirement, str) or not requirement.strip():
            raise SetupError("Each item needs a requirement")
        sam_prompt, min_count = _fix_sam(entry)
        fixed.append({
            "key": key,
            "label": label.strip()[:60],
            "requirement": requirement.strip()[:300],
            "sam_prompt": sam_prompt,
            "min_count": min_count,
            "problem_area": _fix_area(entry.get("problem_area")),
            "critical": bool(entry.get("critical", True)),
            "source": "rule" if entry.get("source") == "rule" else "photo",
        })
    return fixed


def add_room(setup_id, room_type, name, floor):
    """Add one room; returns the new room."""
    if room_type not in ROOM_TYPES:
        raise SetupError(f"Unknown room type {room_type!r}")
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise SetupError("Room name must be 1-80 characters")
    if isinstance(floor, bool) or not isinstance(floor, int):
        raise SetupError("Room floor must be an integer")
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        taken = {room["id"] for room in setup["rooms"]}
        base = slugify(name) or "room"
        room_id, suffix = base, 2
        while room_id in taken:
            room_id, suffix = f"{base}-{suffix}", suffix + 1
        room = _room_entry(room_id, room_type, name, floor)
        setup["rooms"].append(room)
        _save_locked(setup)
        return room


def add_room_photo(setup_id, room_id, filename, data):
    """Store one room photo (check + overview copies); returns {"n": n}."""
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        room = _room_locked(setup, room_id)
        if len(room["photos"]) >= MAX_ROOM_PHOTOS:
            raise SetupError(f"At most {MAX_ROOM_PHOTOS} photos per room")
        if not data:
            raise SetupError("Empty photo upload")
        photos_dir = _setup_dir(setup_id) / "photos" / room_id
        photos_dir.mkdir(parents=True, exist_ok=True)
        # The counter never goes back, so a photo URL (long cache
        # header) can never show an older thumbnail.
        number = room.get("next_photo", 1)
        room["next_photo"] = number + 1
        suffix = Path(filename or "photo.jpg").suffix.lower() or ".jpg"
        fd, tmp_name = tempfile.mkstemp(dir=str(photos_dir), suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            ingest_file(tmp_name, photos_dir, str(number))
        except ValueError as exc:
            raise SetupError(str(exc)) from exc
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        room["photos"].append(number)
        if room["draft"] is not None:
            room["stale"] = True
        _save_locked(setup)
        return {"n": number}


def delete_room_photo(setup_id, room_id, number):
    """Delete one room photo; returns the updated room."""
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        room = _room_locked(setup, room_id)
        if number not in room["photos"]:
            raise SetupNotFound(f"Room '{room_id}' has no photo {number}")
        photos_dir = _setup_dir(setup_id) / "photos" / room_id
        for name in (f"{number}_check.jpg", f"{number}_overview.jpg"):
            try:
                (photos_dir / name).unlink()
            except OSError:
                pass
        room["photos"] = [n for n in room["photos"] if n != number]
        if room["draft"] is not None:
            room["stale"] = True
        _save_locked(setup)
        return room


def read_room_photo(setup_id, room_id, number):
    """One room photo's overview bytes, or None."""
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        room = _room_locked(setup, room_id)
        if number not in room["photos"]:
            return None
        path = _setup_dir(setup_id) / "photos" / room_id / f"{number}_overview.jpg"
        if not path.is_file():
            return None
        return path.read_bytes()


def setup_rule_schema():
    return {
        "type": "object",
        "properties": {
            "english": {"type": "string"},
        },
        "required": ["english"],
        "additionalProperties": False,
    }


def room_voice_note(client, setup_id, room_id, audio_bytes, filename):
    """Transcribe one spoken rule; returns {"status": ...}.

    "ok" carries transcript + english; "no_speech" (empty transcript)
    carries nothing. The audio is never kept.
    """
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        _room_locked(setup, room_id)
        language = setup["language"]
        setup_dir = _setup_dir(setup_id)
    if not audio_bytes:
        raise SetupError("Empty audio upload")
    if len(audio_bytes) > MAX_NOTE_BYTES:
        raise SetupError("Audio too large (rules stop at 60 s)")
    setup_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "rule.webm").suffix.lower() or ".webm"
    fd, tmp_name = tempfile.mkstemp(dir=str(setup_dir), suffix=suffix)
    wav_name = tmp_name + ".wav"
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(audio_bytes)
        convert_to_wav(tmp_name, wav_name)
        with wave.open(wav_name, "rb") as wav:
            seconds = wav.getnframes() / (wav.getframerate() or 1)
        if seconds > MAX_RULE_SECONDS:
            raise SetupError("Spoken rule is too long")
        result = client.transcribe(
            f"setup voice {setup_id} {room_id}", wav_name, [language]
        )
        text = transcript_text(result)
    finally:
        for path in (tmp_name, wav_name):
            try:
                os.unlink(path)
            except OSError:
                pass
    if not text:
        return {"status": "no_speech"}
    if language == "en":
        return {"status": "ok", "transcript": text, "english": text}
    answer = client.respond(
        f"setup rule {setup_id} {room_id}",
        [{"type": "input_text", "text": text}],
        instructions=prompts.setup_rule_instructions(text, language),
        schema=setup_rule_schema(),
    )
    english = answer.get("english", "") if isinstance(answer, dict) else ""
    return {"status": "ok", "transcript": text, "english": english.strip() or text}


def draft_schema():
    return {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "beds": {"type": "array", "items": {"type": "string"}},
            "towels": {"type": "integer"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "label": {"type": "string"},
                        "requirement": {"type": "string"},
                        "sam_prompt": {"type": ["string", "null"]},
                        "min_count": {"type": ["integer", "null"]},
                        "problem_area": {"type": ["string", "null"]},
                        "critical": {"type": "boolean"},
                        "source": {"type": "string", "enum": ["rule", "photo"]},
                    },
                    "required": ["key", "label", "requirement", "critical", "source"],
                    "additionalProperties": False,
                },
            },
            "unclear_rules": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["description", "beds", "towels", "items", "unclear_rules"],
        "additionalProperties": False,
    }


def _fix_key(raw, seen, fallback):
    key = re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower()).strip("_")
    if len(key) < 2 or len(key) > 40 or key == "coverage":
        key = re.sub(r"[^a-z0-9]+", "_", fallback.lower()).strip("_")
    if len(key) < 2 or len(key) > 40 or key == "coverage":
        key = "rule_item"
    base, suffix = key[:40].rstrip("_") or "rule_item", 2
    key = base
    while key in seen:
        key = f"{base}_{suffix}"
        suffix += 1
    seen.add(key)
    return key


def _fix_sam(entry):
    prompt = entry.get("sam_prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.split()) > 3:
        return None, None
    count = entry.get("min_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
        return prompt.strip(), None
    return prompt.strip(), count


def _fix_area(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    if len(raw.split()) > 3 or len(raw) > 40:
        return None
    return raw.strip()


def fixup_draft(answer, room):
    """Fix a model draft in code; never trust the model blindly."""
    if not isinstance(answer, dict):
        raise SetupError("The checklist came back unreadable; tap Make checklist again")
    description = answer.get("description")
    description = description.strip()[:500] if isinstance(description, str) else ""
    beds = []
    if room["type"] == "bedroom":
        raw_beds = answer.get("beds")
        if isinstance(raw_beds, list):
            beds = [b.strip()[:40] for b in raw_beds
                    if isinstance(b, str) and b.strip()][:8]
        beds = beds or ["bed"]
    towels = 0
    if room["type"] == "bathroom":
        raw = answer.get("towels")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
            towels = min(raw, 20)
        else:
            towels = 2
    raw_items = answer.get("items")
    if not isinstance(raw_items, list):
        raise SetupError("The checklist came back unreadable; tap Make checklist again")
    standard_keys = {check["key"] for check in standard_checks_for(room["type"])}
    seen = set()
    rules, photos = [], []
    for number, entry in enumerate(raw_items, start=1):
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        label = label.strip()[:60] if isinstance(label, str) else ""
        requirement = entry.get("requirement")
        requirement = requirement.strip()[:300] if isinstance(requirement, str) else ""
        key = _fix_key(entry.get("key"), seen, label or f"rule_{number}")
        sam_prompt, min_count = _fix_sam(entry)
        source = "rule" if entry.get("source") == "rule" else "photo"
        if source == "photo" and key in standard_keys:
            continue  # a photo item repeating a standard check
        fixed = {
            "key": key,
            "label": label or key,
            "requirement": requirement or label or key,
            "sam_prompt": sam_prompt,
            "min_count": min_count,
            "problem_area": _fix_area(entry.get("problem_area")),
            "critical": bool(entry.get("critical", True)),
            "source": source,
        }
        (rules if fixed["source"] == "rule" else photos).append(fixed)
    items = rules + photos[: min(3, max(0, 12 - len(rules)))]
    items = items[:12]
    unclear = answer.get("unclear_rules")
    unclear = [u.strip()[:200] for u in unclear
               if isinstance(u, str) and u.strip()][:20] if isinstance(unclear, list) else []
    return {
        "description": description or room["name"],
        "beds": beds,
        "towels": towels,
        "items": items,
        "unclear_rules": unclear,
    }


def standard_checks_for(room_type):
    """The standard (key, label, requirement) checks for one room type."""
    probe = {"id": "probe", "type": room_type, "name": "Probe",
             "floor": 1, "beds": 1, "towels": 2}
    return [
        {"key": spec["key"], "label": spec["label"],
         "requirement": spec["requirement"]}
        for spec in items_for_room(probe)
        if spec["key"] != "coverage"
    ]


def draft_room(client, setup_id, room_id, effort=None, model=None, store=True):
    """Draft one room's checklist from its rules + photos; returns the draft.

    The setup lock is released during the model call (runs.py pattern):
    the read snapshots what it needs, the call runs free, then the
    result is stored under the lock again. A room deleted meanwhile
    drops the result; changed rules or photos store it but mark the
    room stale.
    """
    effort = config.SETUP_EFFORT if effort is None else effort
    model = config.SETUP_MODEL if model is None else model
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        room = _room_locked(setup, room_id)
        if not room["photos"]:
            raise SetupError("Take at least one photo of the room first")
        photos_dir = _setup_dir(setup_id) / "photos" / room_id
        snapshot = {
            "name": room["name"],
            "type": room["type"],
            "rules": room["rules"],
            "photos": sorted(room["photos"]),
        }
        content = []
        for number in snapshot["photos"]:
            check_path = photos_dir / f"{number}_check.jpg"
            if not check_path.is_file():
                raise SetupError(f"Room '{room['name']}' is missing photo {number}")
            fake = SimpleNamespace(
                id=f"P{number:02d}", check_path=check_path,
                overview_path=photos_dir / f"{number}_overview.jpg",
            )
            content.extend(prompts.photo_parts(fake, prompts.SETUP_PHOTO_SIZE))
    answer = client.respond(
        f"setup draft {setup_id} {room_id}",
        content,
        instructions=prompts.setup_draft_instructions(
            snapshot["name"], snapshot["type"], snapshot["rules"],
            standard_checks_for(snapshot["type"]),
        ),
        schema=draft_schema(),
        effort=effort,
        model=model,
    )
    draft = fixup_draft(answer, snapshot)
    if not store:
        return draft
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        try:
            room = _room_locked(setup, room_id)
        except SetupNotFound:
            return draft  # room deleted meanwhile: drop the stored result
        room["draft"] = draft
        room["status"] = "ready"
        room["stale"] = (
            room["rules"] != snapshot["rules"]
            or sorted(room["photos"]) != snapshot["photos"]
        )
        _save_locked(setup)
        return draft


def _claim_property_id(name):
    base = slugify(name) or "house"
    candidate, suffix = base, 2
    while candidate == "demo-house" or (config.PROPERTIES_DIR / candidate).is_dir():
        candidate, suffix = f"{base}-{suffix}", suffix + 1
    return candidate


def save_setup(client, setup_id):
    """Write property.json + tour; returns counts (or tour_error).

    A setup that was saved before reuses its property id (the files
    are overwritten and the tour is planned again); only a setup that
    was never saved claims a new id.
    """
    if not _claim_saving(setup_id):
        raise SetupBusy("Already saving")
    try:
        return _save_now(client, setup_id)
    finally:
        _release_saving(setup_id)


def _save_now(client, setup_id):
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        pending = [room["name"] for room in setup["rooms"]
                   if room["status"] not in ("ready", "skipped")
                   or (room["status"] == "ready" and room["draft"] is None)]
        if pending:
            raise SetupError(
                "These rooms have no checklist yet: " + ", ".join(pending)
            )
        stale = [room["name"] for room in setup["rooms"]
                 if room["status"] == "ready" and room.get("stale")]
        if stale:
            raise SetupError(
                "These rooms have new rules that are not in their checklist yet: "
                + ", ".join(stale)
            )
        if all(room["status"] == "skipped" for room in setup["rooms"]):
            raise SetupError("At least one room must be ready")
        rooms_out, extras = [], []
        for room in setup["rooms"]:
            if room["status"] == "skipped":
                continue
            draft = room["draft"]
            entry = {
                "id": room["id"],
                "type": room["type"],
                "name": room["name"],
                "floor": room["floor"],
                "description": draft["description"],
            }
            if room["type"] == "bedroom":
                entry["beds"] = draft["beds"]
            if room["type"] == "bathroom":
                entry["towels"] = draft["towels"]
            rooms_out.append(entry)
            for item in draft["items"]:
                extras.append({
                    "room": room["id"],
                    "key": item["key"],
                    "label": item["label"],
                    "requirement": item["requirement"],
                    "sam_prompt": item["sam_prompt"],
                    "min_count": item["min_count"],
                    "critical": item["critical"],
                    "problem_area": item["problem_area"],
                })
        property_id = setup.get("property_id") or _claim_property_id(setup["name"])
        property = validate_property({
            "id": property_id,
            "name": setup["name"],
            "cleaner_language": "es",
            "host_language": "en",
            "rooms": rooms_out,
            "extra_items": extras,
        }, property_id)
        target = config.PROPERTIES_DIR / property_id
        target.mkdir(parents=True, exist_ok=True)
        _atomic_write(target / "property.json", property)
        checklist = build_checklist(property)
        result = {
            "property_id": property_id,
            "rooms": len(rooms_out),
            "items": len(checklist),
        }
    # The tour call runs without the lock; reads and edits keep working.
    try:
        shots, _notes = generate_tour(client, property, checklist)
    except PropertyError as exc:
        with _lock_for(setup_id):
            setup = load_setup(setup_id)
            setup["property_id"] = property_id
            setup["save_result"] = dict(result, tour_error=str(exc))
            _save_locked(setup)
        return dict(result, tour_error=str(exc))
    result["shots"] = len(shots)
    _save_setup_examples(setup_id, property_id, shots)
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        setup["property_id"] = property_id
        setup["save_result"] = dict(result)
        _save_locked(setup)
    return result


def _save_setup_examples(setup_id, property_id, shots):
    """Every shot gets its room's first setup photo as its example."""
    try:
        setup = load_setup(setup_id)
    except SetupNotFound:
        return
    rooms_by_id = {room["id"]: room for room in setup["rooms"]}
    for shot in shots:
        room = rooms_by_id.get(shot["room_id"])
        if not room or not room.get("photos"):
            continue
        if examples_mod.has_example(property_id, shot["shot_id"]):
            continue
        number = min(room["photos"])
        path = (
            _setup_dir(setup_id) / "photos" / room["id"]
            / f"{number}_overview.jpg"
        )
        if path.is_file():
            examples_mod.save_example(property_id, shot["shot_id"], path)


def retry_tour(client, setup_id):
    """Plan the photo tour again after a failed tour call."""
    if not _claim_saving(setup_id):
        raise SetupBusy("Already saving")
    try:
        return _retry_tour_now(client, setup_id)
    finally:
        _release_saving(setup_id)


def _retry_tour_now(client, setup_id):
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        property_id = setup.get("property_id")
        if not property_id:
            raise SetupError("Save the house before planning its tour")
    property = load_property(property_id)
    try:
        shots, _notes = generate_tour(client, property, build_checklist(property))
    except PropertyError as exc:
        return {"property_id": property_id, "tour_error": str(exc)}
    with _lock_for(setup_id):
        setup = load_setup(setup_id)
        result = dict(setup.get("save_result") or {}, property_id=property_id)
        result["shots"] = len(shots)
        result.pop("tour_error", None)
        setup["save_result"] = result
        _save_locked(setup)
    return {"property_id": property_id, "shots": len(shots)}
