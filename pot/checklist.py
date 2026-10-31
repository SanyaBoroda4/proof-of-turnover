"""House plan loading and checklist generation.

load_property(property_id) reads properties/<id>/property.json and
validates it; build_checklist(property) generates every room's items
from pot.rooms templates plus any custom extra_items.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pot.rooms import ROOM_TYPES, items_for_room

DEFAULT_PROPERTIES_DIR = Path(__file__).resolve().parent.parent / "properties"


class PropertyError(ValueError):
    """A property file is missing, invalid or incomplete."""


@dataclass
class ChecklistItem:
    id: str
    room_id: str
    room_name: str
    floor: Optional[int]
    key: str
    label: str
    requirement: str
    sam_prompt: Optional[str]
    min_count: Optional[int]
    critical: bool


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _need_str(mapping, field, where):
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PropertyError(f"{where}: field '{field}' must be a non-empty string")
    return value


def _need_positive_int(mapping, field, where):
    value = mapping.get(field)
    if not _is_int(value) or value <= 0:
        raise PropertyError(f"{where}: field '{field}' must be a positive integer")
    return value


def _need_beds(mapping, where):
    value = mapping.get("beds")
    if isinstance(value, list):
        if not value or any(not isinstance(name, str) or not name.strip() for name in value):
            raise PropertyError(
                f"{where}: field 'beds' must be a positive integer or a "
                "non-empty list of bed names"
            )
        return value
    return _need_positive_int(mapping, "beds", where)


def _validate_room(data, index, property_id, seen_ids):
    where = f"Property '{property_id}', room #{index + 1}"
    if not isinstance(data, dict):
        raise PropertyError(f"{where}: must be an object")
    room_id = _need_str(data, "id", where)
    if room_id in seen_ids:
        raise PropertyError(f"Property '{property_id}': duplicate room id '{room_id}'")
    seen_ids.add(room_id)
    where = f"Property '{property_id}', room '{room_id}'"
    room_type = data.get("type")
    if room_type not in ROOM_TYPES:
        raise PropertyError(
            f"{where}: field 'type' must be one of {', '.join(ROOM_TYPES)} "
            f"(got {room_type!r})"
        )
    _need_str(data, "name", where)
    if not _is_int(data.get("floor")):
        raise PropertyError(f"{where}: field 'floor' must be an integer")
    if room_type == "bedroom" and data.get("beds") is None:
        raise PropertyError(f"{where}: field 'beds' is required for a bedroom")
    if room_type == "bathroom" and data.get("towels") is None:
        raise PropertyError(f"{where}: field 'towels' is required for a bathroom")
    if data.get("beds") is not None:
        _need_beds(data, where)
    for field in ("towels", "coffee_pods"):
        if data.get(field) is not None:
            _need_positive_int(data, field, where)
    if data.get("description") is not None:
        _need_str(data, "description", where)
    return data


def _validate_extra(item, index, property_id, rooms_by_id):
    where = f"Property '{property_id}', extra item #{index + 1}"
    if not isinstance(item, dict):
        raise PropertyError(f"{where}: must be an object")
    for field in ("key", "label", "requirement"):
        _need_str(item, field, where)
    room = item.get("room")
    if room is not None and room not in rooms_by_id:
        raise PropertyError(f"{where}: unknown room '{room}'")
    sam_prompt = item.get("sam_prompt")
    if sam_prompt is not None and not isinstance(sam_prompt, str):
        raise PropertyError(f"{where}: field 'sam_prompt' must be a string or null")
    min_count = item.get("min_count")
    if min_count is not None and (not _is_int(min_count) or min_count <= 0):
        raise PropertyError(f"{where}: field 'min_count' must be a positive integer or null")
    critical = item.get("critical", True)
    if not isinstance(critical, bool):
        raise PropertyError(f"{where}: field 'critical' must be true or false")
    return item


def load_property(property_id, properties_dir=None):
    """Load and validate properties/<property_id>/property.json."""
    base = Path(properties_dir) if properties_dir is not None else DEFAULT_PROPERTIES_DIR
    path = base / property_id / "property.json"
    if not path.is_file():
        raise PropertyError(f"Property '{property_id}': file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PropertyError(f"Property '{property_id}': invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PropertyError(f"Property '{property_id}': file must contain an object")
    where = f"Property '{property_id}'"
    for field in ("id", "name", "cleaner_language", "host_language"):
        _need_str(data, field, where)
    rooms = data.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise PropertyError(f"{where}: field 'rooms' must be a non-empty list")
    seen_ids = set()
    data["rooms"] = [_validate_room(room, i, property_id, seen_ids) for i, room in enumerate(rooms)]
    extra_items = data.get("extra_items", [])
    if not isinstance(extra_items, list):
        raise PropertyError(f"{where}: field 'extra_items' must be a list")
    rooms_by_id = {room["id"]: room for room in data["rooms"]}
    data["extra_items"] = [
        _validate_extra(item, i, property_id, rooms_by_id) for i, item in enumerate(extra_items)
    ]
    seen_extra_ids = set()
    for item in data["extra_items"]:
        extra_id = f"{item.get('room') or 'house'}.{item['key']}"
        if extra_id in seen_extra_ids:
            raise PropertyError(
                f"Property '{property_id}': duplicate extra item id '{extra_id}'"
            )
        seen_extra_ids.add(extra_id)
    return data


def build_checklist(property):
    """Generate all checklist items, ids like "master-bathroom.towels"."""
    items = []
    for room in property["rooms"]:
        for spec in items_for_room(room):
            items.append(
                ChecklistItem(
                    id=f"{room['id']}.{spec['key']}",
                    room_id=room["id"],
                    room_name=room["name"],
                    floor=room["floor"],
                    key=spec["key"],
                    label=spec["label"],
                    requirement=spec["requirement"],
                    sam_prompt=spec["sam_prompt"],
                    min_count=spec["min_count"],
                    critical=spec["critical"],
                )
            )
    rooms_by_id = {room["id"]: room for room in property["rooms"]}
    by_id = {item.id: index for index, item in enumerate(items)}
    for extra in property.get("extra_items", []):
        room = rooms_by_id.get(extra.get("room"))
        if room is None:
            room_id, room_name, floor = "house", "Whole house", None
        else:
            room_id, room_name, floor = room["id"], room["name"], room["floor"]
        replacement = ChecklistItem(
            id=f"{room_id}.{extra['key']}",
            room_id=room_id,
            room_name=room_name,
            floor=floor,
            key=extra["key"],
            label=extra["label"],
            requirement=extra["requirement"],
            sam_prompt=extra.get("sam_prompt"),
            min_count=extra.get("min_count"),
            critical=extra.get("critical", True),
        )
        if replacement.id in by_id:
            # An extra with the same room and key replaces the template item.
            items[by_id[replacement.id]] = replacement
        else:
            by_id[replacement.id] = len(items)
            items.append(replacement)
    return items


def main(argv):
    if len(argv) != 2:
        print("Usage: python -m pot.checklist <property-id>", file=sys.stderr)
        return 1
    try:
        property = load_property(argv[1])
    except PropertyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    items = build_checklist(property)
    print(f"{property['name']} - {len(items)} items")
    rooms = sorted(property["rooms"], key=lambda room: room["floor"])
    for room in rooms:
        room_items = [item for item in items if item.room_id == room["id"]]
        print(f"Floor {room['floor']} - {room['name']} ({room['type']})")
        for item in room_items:
            flag = "critical" if item.critical else "optional"
            print(f"  [{flag}] {item.id}: {item.label} - {item.requirement}")
    house_items = [item for item in items if item.room_id == "house"]
    if house_items:
        print("Whole house")
        for item in house_items:
            flag = "critical" if item.critical else "optional"
            print(f"  [{flag}] {item.id}: {item.label} - {item.requirement}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
