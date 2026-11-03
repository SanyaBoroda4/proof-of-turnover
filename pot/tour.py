"""Shot plans for the guided tour: one photo per shot, room by room.

generate_tour() asks Muse Spark once per property (checklist plus the
floor plan picture when properties/<id>/house-plan.png exists) and saves
the result as properties/<id>/tour.json, which the host may edit.
fixup_tour() checks any shot list: only known item ids, every critical
item in at least one shot; a missed item gets its own shot added at the
end of its room.
"""

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

from pot import config, prompts
from pot.checklist import PropertyError, build_checklist, load_property
from pot.meta_client import MetaClient, MetaClientError

TOUR_FILENAME = "tour.json"
HOUSE_PLAN_FILENAME = "house-plan.png"


def tour_schema():
    return {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "shot_id": {"type": "string"},
                        "room_id": {"type": "string"},
                        "room_name_es": {"type": "string"},
                        "room_name_en": {"type": "string"},
                        "room_name_ru": {"type": "string"},
                        "short_es": {"type": "string"},
                        "short_en": {"type": "string"},
                        "short_ru": {"type": "string"},
                        "item_ids": {"type": "array", "items": {"type": "string"}},
                        "overview": {"type": "boolean"},
                        "instruction_es": {"type": "string"},
                        "instruction_en": {"type": "string"},
                        "instruction_ru": {"type": "string"},
                    },
                    "required": ["shot_id", "room_id", "room_name_es", "room_name_en",
                                 "room_name_ru", "short_es", "short_en", "short_ru",
                                 "item_ids", "instruction_es", "instruction_en",
                                 "instruction_ru"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["shots"],
        "additionalProperties": False,
    }


def _property_dir(property_id, properties_dir=None):
    base = Path(properties_dir) if properties_dir is not None else config.PROPERTIES_DIR
    return base / property_id


def fixup_tour(property, checklist, shots):
    """Validate shots; returns (shots, notes).

    Unknown rooms, duplicate shot ids and unknown item ids are dropped;
    coverage items are stripped (any room photo covers them); every
    critical item missing from all shots gets its own shot at the end of
    its room (whole-house items join the last shot).
    """
    notes = []
    by_id = {item.id: item for item in checklist}
    rooms = {room["id"]: room for room in property["rooms"]}
    fixed = []
    seen_shots = set()
    for entry in shots or []:
        if not isinstance(entry, dict):
            notes.append("Tour: skipped a malformed shot")
            continue
        shot_id = entry.get("shot_id") or ""
        room_id = entry.get("room_id") or ""
        if not shot_id or shot_id in seen_shots:
            notes.append(f"Tour: skipped shot {shot_id!r} (missing or duplicate id)")
            continue
        if room_id not in rooms:
            notes.append(f"Tour: skipped shot {shot_id!r} (unknown room {room_id!r})")
            continue
        item_ids = []
        for item_id in entry.get("item_ids") or []:
            item = by_id.get(item_id)
            if item is None:
                notes.append(f"Tour: shot {shot_id!r} cites unknown item {item_id!r}")
                continue
            if item.key == "coverage":
                continue
            if item_id not in item_ids:
                item_ids.append(item_id)
        seen_shots.add(shot_id)
        shot = {
            "shot_id": shot_id,
            "room_id": room_id,
            "item_ids": list(item_ids),
            "overview": entry.get("overview") is True,
        }
        room_en = entry.get("room_name_en") or rooms[room_id]["name"]
        short_en = entry.get("short_en") or room_en
        instruction_en = entry.get("instruction_en") or ""
        if not entry.get("room_name_en"):
            notes.append(f"Tour: shot {shot_id!r} has no English room name")
        if not entry.get("short_en"):
            notes.append(f"Tour: shot {shot_id!r} has no English short name")
        if not entry.get("instruction_en"):
            notes.append(f"Tour: shot {shot_id!r} has no English instruction")
        for field, english in (("room_name", room_en), ("short", short_en),
                               ("instruction", instruction_en)):
            shot[f"{field}_en"] = english
            for lang in ("es", "ru"):
                key = f"{field}_{lang}"
                shot[key] = entry.get(key) or english
                if not entry.get(key):
                    notes.append(
                        f"Tour: shot {shot_id!r} has no {key}, English used"
                    )
        fixed.append(shot)
    # An overview shot checks the whole room: its items become every
    # non-coverage item of its room (detail shots keep their own items).
    # Whole-house items already placed on the shot stay on it.
    room_items = {}
    for item in checklist:
        if item.key != "coverage" and item.room_id != "house":
            room_items.setdefault(item.room_id, []).append(item.id)
    for shot in fixed:
        if shot.get("overview"):
            expanded = list(room_items.get(shot["room_id"], []))
            for item_id in shot["item_ids"]:
                item = by_id.get(item_id)
                if item is not None and item.room_id == "house" \
                        and item_id not in expanded:
                    expanded.append(item_id)
            shot["item_ids"] = expanded
    covered = set()
    for shot in fixed:
        covered.update(shot["item_ids"])
    missing = [
        item for item in checklist
        if item.critical and item.key != "coverage" and item.id not in covered
    ]
    extra = 0
    for item in missing:
        if item.room_id == "house":
            if not fixed:
                notes.append(f"Tour: no shot to attach whole-house item {item.id!r}")
                continue
            fixed[-1]["item_ids"].append(item.id)
            notes.append(f"Tour: attached {item.id} to the last shot")
            continue
        extra += 1
        shot_id = f"{item.room_id}-extra-{extra}"
        room_name = rooms[item.room_id]["name"]
        fixed.append({
            "shot_id": shot_id,
            "room_id": item.room_id,
            "room_name_es": room_name,
            "room_name_en": room_name,
            "room_name_ru": room_name,
            "short_es": room_name,
            "short_en": room_name,
            "short_ru": room_name,
            "item_ids": [item.id],
            "overview": False,
            "instruction_es": f"Foto: {item.label}",
            "instruction_en": item.requirement,
            "instruction_ru": item.requirement,
        })
        notes.append(f"Tour: added shot {shot_id} for missing item {item.id}")
    ordered = []
    extras = [shot for shot in fixed if "-extra-" in shot["shot_id"]]
    plain = [shot for shot in fixed if "-extra-" not in shot["shot_id"]]
    for shot in plain:
        ordered.append(shot)
    by_room_extras = {}
    for shot in extras:
        by_room_extras.setdefault(shot["room_id"], []).append(shot)
    result = []
    for shot in ordered:
        result.append(shot)
    # Move each extra shot right after its room's last plain shot.
    for room_id, room_extras in by_room_extras.items():
        positions = [i for i, shot in enumerate(result) if shot["room_id"] == room_id]
        insert_at = positions[-1] + 1 if positions else len(result)
        for shot in room_extras:
            result.insert(insert_at, shot)
            insert_at += 1
    return result, notes


def save_tour(property_id, shots, properties_dir=None, meta=None):
    path = _property_dir(property_id, properties_dir) / TOUR_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"shots": shots}
    if meta:
        data.update(meta)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_tour(property_id, properties_dir=None):
    """Load and fix up a property's tour; raises PropertyError when missing."""
    path = _property_dir(property_id, properties_dir) / TOUR_FILENAME
    if not path.is_file():
        raise PropertyError(f"Property '{property_id}': no tour.json yet (POST /api/tour)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PropertyError(f"Property '{property_id}': invalid tour.json: {exc}") from exc
    return data.get("shots", [])


def generate_tour(client, property, checklist, effort=None, properties_dir=None):
    """Ask Muse Spark for the shot plan; saves tour.json; returns (shots, notes)."""
    plan_path = _property_dir(property["id"], properties_dir) / HOUSE_PLAN_FILENAME
    content = [
        {"type": "input_text", "text": "Plan the cleaner's photo tour."},
    ]
    if plan_path.is_file():
        raw = base64.b64encode(plan_path.read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{raw}"})
    try:
        answer = client.respond(
            f"tour {property['id']}",
            content,
            instructions=prompts.tour_instructions(
                property, checklist, plan_path.is_file()
            ),
            schema=tour_schema(),
            effort=prompts.REASONING_EFFORT if effort is None else effort,
            # Three languages per shot roughly triples the answer size.
            max_output_tokens=32000,
        )
    except MetaClientError as exc:
        raise PropertyError(f"Property '{property['id']}': tour generation failed: {exc}") from exc
    raw = answer.get("shots") if isinstance(answer, dict) else None
    if not isinstance(raw, list):
        raise PropertyError(f"Property '{property['id']}': tour answer has no shots list")
    shots, notes = fixup_tour(property, checklist, raw)
    if not shots:
        raise PropertyError(f"Property '{property['id']}': tour has no usable shots")
    save_tour(property["id"], shots, properties_dir, meta={
        "model": getattr(client, "reasoning_model", "unknown"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    return shots, notes


def main(argv):
    parser = argparse.ArgumentParser(prog="pot.tour")
    parser.add_argument("property")
    args = parser.parse_args(argv[1:])
    try:
        property = load_property(args.property)
    except PropertyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        shots, notes = generate_tour(MetaClient(), property, build_checklist(property))
    except (PropertyError, MetaClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Tour for {property['name']}: {len(shots)} shots")
    for shot in shots:
        print(f"  {shot['shot_id']} [{shot['room_id']}] "
              f"{shot['instruction_en']} ({len(shot['item_ids'])} items)")
    for note in notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
