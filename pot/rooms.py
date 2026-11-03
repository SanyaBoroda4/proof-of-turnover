"""Room templates: the checklist items generated for each room type.

Each template item has key, label, requirement (plain-language rule, using
the room's numbers where they matter), sam_prompt (short noun for SAM, or
None), min_count (int, room-field name, or None) and critical. An item
with only_if is generated only when that room field is set.
"""

ROOM_TYPES = (
    "bedroom",
    "bathroom",
    "half_bathroom",
    "kitchen",
    "dining_room",
    "living_room",
    "office",
    "hallway",
    "laundry",
    "outdoor",
    "other",
)


def _plural(count, one, many):
    return one if count == 1 else many


TEMPLATES = {
    "bedroom": [
        {
            "key": "beds_made",
            "label": "Beds made",
            "requirement": lambda room: (
                f"All {room['beds']} {_plural(room['beds'], 'bed', 'beds')} made "
                "(sheets and blanket pulled up, pillows in place)"
            ),
            "sam_prompt": "bed",
            "min_count": "beds",
            "critical": True,
        },
        {
            "key": "no_belongings",
            "label": "No guest belongings left",
            "requirement": "No guest belongings, trash or laundry left in the room",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "floor_clear",
            "label": "Floor clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
    "bathroom": [
        {
            "key": "towels",
            "label": "Bath towels",
            "requirement": lambda room: (
                f"At least {room['towels']} clean folded "
                f"{_plural(room['towels'], 'bath towel', 'bath towels')}"
            ),
            "sam_prompt": "towel",
            "min_count": "towels",
            "critical": True,
        },
        {
            "key": "toilet_paper",
            "label": "Toilet paper",
            "requirement": "Toilet paper on the holder plus a spare roll",
            "sam_prompt": "toilet paper roll",
            "min_count": 2,
            "critical": True,
        },
        {
            "key": "toilet",
            "label": "Toilet clean",
            "requirement": "Toilet bowl, seat and base clean",
            "sam_prompt": "toilet",
            "min_count": 1,
            "critical": True,
        },
        {
            "key": "shower",
            "label": "Shower or tub clean",
            "requirement": "Shower or tub is clean, no hair or used bottles",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "sink",
            "label": "Sink, counter and mirror clean",
            "requirement": "Sink, counter and mirror are clean",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "hand_towel",
            "label": "Hand towels hanging",
            "requirement": lambda room: (
                f"{_sinks(room)} clean "
                f"{_plural(_sinks(room), 'hand towel', 'hand towels')} "
                "hanging, one at each sink"
            ),
            "sam_prompt": "hand towel",
            "min_count": "sinks",
            "critical": True,
        },
        {
            "key": "hand_soap",
            "label": "Hand soap at the sink",
            "requirement": "A hand soap dispenser at the sink",
            "sam_prompt": "soap dispenser",
            "min_count": 1,
            "critical": True,
        },
    ],
    "kitchen": [
        {
            "key": "counters",
            "label": "Counters and sink clear",
            "requirement": "Counters and sink are clear of dishes and food",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "trash",
            "label": "Trash emptied",
            "requirement": "Trash is empty with a fresh liner in the bin",
            "sam_prompt": "trash can",
            "min_count": 1,
            "critical": True,
        },
        {
            "key": "coffee_pods",
            "label": "Coffee pods stocked",
            "requirement": lambda room: (
                f"At least {room['coffee_pods']} "
                f"{_plural(room['coffee_pods'], 'coffee pod', 'coffee pods')} stocked"
            ),
            "sam_prompt": "coffee pod",
            "min_count": "coffee_pods",
            "critical": False,
            "only_if": "coffee_pods",
        },
    ],
    "living_room": [
        {
            "key": "couch",
            "label": "Couch clean",
            "requirement": "Couch is clean with nothing left on it",
            "sam_prompt": "couch",
            "min_count": 1,
            "critical": True,
        },
        {
            "key": "no_belongings",
            "label": "No guest belongings left",
            "requirement": "No guest belongings or trash left in the room",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "floor_clear",
            "label": "Floor clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
    "outdoor": [
        {
            "key": "area_tidy",
            "label": "Outdoor area tidy",
            "requirement": "Furniture is in place, no trash left",
            "sam_prompt": None,
            "min_count": None,
            "critical": False,
        },
    ],
    "half_bathroom": [
        {
            "key": "toilet",
            "label": "Toilet clean",
            "requirement": "Toilet bowl, seat and base clean",
            "sam_prompt": "toilet",
            "min_count": 1,
            "critical": True,
        },
        {
            "key": "toilet_paper",
            "label": "Toilet paper",
            "requirement": "Toilet paper on the holder",
            "sam_prompt": "toilet paper roll",
            "min_count": 1,
            "critical": True,
        },
        {
            "key": "sink",
            "label": "Sink and mirror clean",
            "requirement": "Sink and mirror are clean",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "hand_towel",
            "label": "Hand towels hanging",
            "requirement": lambda room: (
                f"{_sinks(room)} clean "
                f"{_plural(_sinks(room), 'hand towel', 'hand towels')} "
                "hanging, one at each sink"
            ),
            "sam_prompt": "hand towel",
            "min_count": "sinks",
            "critical": True,
        },
        {
            "key": "hand_soap",
            "label": "Hand soap at the sink",
            "requirement": "A hand soap dispenser at the sink",
            "sam_prompt": "soap dispenser",
            "min_count": 1,
            "critical": True,
        },
    ],
    "dining_room": [
        {
            "key": "table",
            "label": "Table clear",
            "requirement": "Table is clear and wiped",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "chairs",
            "label": "Chairs in place",
            "requirement": "Chairs are in place",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "floor_clear",
            "label": "Floor clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
    "laundry": [
        {
            "key": "machines",
            "label": "Washer and dryer empty",
            "requirement": "Washer and dryer are empty",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "floor_clear",
            "label": "Floor clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
    "office": [
        {
            "key": "desk",
            "label": "Desk clear",
            "requirement": "Desk is clear",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "no_belongings",
            "label": "No guest belongings left",
            "requirement": "No guest belongings left in the room",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "floor_clear",
            "label": "Floor clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
    "hallway": [
        {
            "key": "floor_clear",
            "label": "Floors and stairs clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
    "other": [
        {
            "key": "tidy",
            "label": "Tidy",
            "requirement": "Tidy, no guest belongings left",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
        {
            "key": "floor_clear",
            "label": "Floor clear",
            "requirement": "Floor is clean: nothing left on it, no spots, spills or crumbs",
            "sam_prompt": None,
            "min_count": None,
            "critical": True,
        },
    ],
}

COVERAGE_ITEM = {
    "key": "coverage",
    "label": "Room photographed",
    "requirement": "At least one clear photo of this room",
    "sam_prompt": None,
    "min_count": None,
    "critical": True,
}


def _sinks(room):
    return room.get("sinks") or 1


def _resolve_min_count(spec, room):
    value = spec["min_count"]
    if isinstance(value, str):
        if value == "sinks":
            return _sinks(room)
        return room[value]
    return value


def _render(spec, room):
    requirement = spec["requirement"]
    if callable(requirement):
        requirement = requirement(room)
    return {
        "key": spec["key"],
        "label": spec["label"],
        "requirement": requirement,
        "sam_prompt": spec["sam_prompt"],
        "min_count": _resolve_min_count(spec, room),
        "critical": spec["critical"],
        "problem_area": spec.get("problem_area"),
    }


def _bed_items(room):
    """One made-bed item per named bed (beds given as a list)."""
    items = []
    for number, name in enumerate(room["beds"], start=1):
        titled = name[:1].upper() + name[1:]
        items.append(
            {
                "key": f"bed_{number}",
                "label": f"{titled} made",
                "requirement": (
                    f"{titled} made: sheets and blanket pulled up, pillows in place"
                ),
                "sam_prompt": "bed",
                "min_count": 1,
                "critical": True,
                "problem_area": None,
            }
        )
    return items


def items_for_room(room):
    """Resolve the template items for one room dict, coverage first."""
    items = [_render(COVERAGE_ITEM, room)]
    for spec in TEMPLATES[room["type"]]:
        if spec["key"] == "beds_made" and isinstance(room.get("beds"), list):
            items.extend(_bed_items(room))
            continue
        only_if = spec.get("only_if")
        if only_if is not None and room.get(only_if) is None:
            continue
        items.append(_render(spec, room))
    return items
