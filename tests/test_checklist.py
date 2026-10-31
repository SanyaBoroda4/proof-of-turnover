"""Offline tests for property loading and checklist generation."""

import json

import pytest

from pot.checklist import PropertyError, build_checklist, load_property


def test_demo_house_descriptions_kept_on_rooms():
    rooms = {room["id"]: room for room in load_property("demo-house")["rooms"]}
    assert rooms["master-bathroom"]["description"] == "double sink, grey tiles"
    assert all(room.get("description") for room in rooms.values())


def _by_id(items):
    return {item.id: item for item in items}


def test_rental_1_v3_checklist():
    prop = load_property("rental-1")
    items = _by_id(build_checklist(prop))
    assert len(items) == 80
    assert items["kitchen.coffee_beans"].critical is True
    assert items["kitchen.coffee_machine_clean"].critical is False
    assert items["kitchen.paper_towels"].min_count == 1
    assert items["kitchen.fridge"].critical is True
    assert items["kitchen.fridge_left_door"].critical is True
    assert items["kitchen.fridge_right_door"].critical is True
    assert items["living-room.sofa_bed_linens"].min_count == 1
    assert items["laundry.machines"].label == "Washer empty"
    assert items["laundry.dryer"].label == "Dryer empty"
    rooms = {room["id"]: room for room in prop["rooms"]}
    assert rooms["patio"]["name"] == "Back porch"
    assert rooms["porch"]["name"] == "Front porch"
    assert "only the outdoor furniture and its pillows" in items["patio.area_tidy"].requirement
    assert "only the outdoor furniture and its pillows" in items["porch.area_tidy"].requirement


def test_demo_house_checklist():
    items = _by_id(build_checklist(load_property("demo-house")))
    assert len(items) == 30
    assert items["master-bedroom.beds_made"].min_count == 1
    assert items["guest-bedroom.beds_made"].min_count == 2
    assert items["master-bathroom.towels"].min_count == 4
    assert items["hall-bathroom.towels"].min_count == 2
    assert items["master-bathroom.toilet_paper"].min_count == 2
    assert items["master-bathroom.toilet"].min_count == 1
    assert items["kitchen.trash"].min_count == 1
    assert items["living-room.couch"].min_count == 1
    coffee = items["kitchen.coffee_pods"]
    assert coffee.min_count == 3
    assert coffee.critical is False
    assert items["patio.area_tidy"].critical is False
    assert items["patio.coverage"].critical is True
    towels = items["master-bathroom.towels"]
    assert towels.room_id == "master-bathroom"
    assert towels.room_name == "Master bathroom"
    assert towels.floor == 2
    assert "4 clean folded bath towels" in towels.requirement
    assert towels.sam_prompt == "towel"


def _write_property(tmp_path, property_id, data):
    folder = tmp_path / property_id
    folder.mkdir()
    (folder / "property.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def _valid_property(**overrides):
    data = {
        "id": "test-house",
        "name": "Test House",
        "cleaner_language": "es",
        "host_language": "en",
        "rooms": [
            {"id": "kitchen", "type": "kitchen", "name": "Kitchen", "floor": 1},
            {"id": "bath", "type": "bathroom", "name": "Bath", "floor": 1, "towels": 2},
        ],
    }
    data.update(overrides)
    return data


def test_missing_bathroom_towels_raises_clear_error(tmp_path):
    data = _valid_property()
    del data["rooms"][1]["towels"]
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="towels"):
        load_property("test-house", properties_dir=base)


def test_missing_rooms_field_raises_clear_error(tmp_path):
    data = _valid_property()
    del data["rooms"]
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="rooms"):
        load_property("test-house", properties_dir=base)


def test_bad_description_raises_clear_error(tmp_path):
    data = _valid_property()
    data["rooms"][0]["description"] = 42
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="description"):
        load_property("test-house", properties_dir=base)


def test_unknown_room_type_raises_clear_error(tmp_path):
    data = _valid_property()
    data["rooms"][0]["type"] = "garage"
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="type"):
        load_property("test-house", properties_dir=base)


def test_new_room_types_build_with_coverage_first(tmp_path):
    rooms = [
        {"id": "wc", "type": "half_bathroom", "name": "WC", "floor": 1},
        {"id": "dining", "type": "dining_room", "name": "Dining", "floor": 1},
        {"id": "wash", "type": "laundry", "name": "Wash", "floor": 1},
        {"id": "study", "type": "office", "name": "Study", "floor": 2},
        {"id": "hall", "type": "hallway", "name": "Hall", "floor": 1},
        {"id": "den", "type": "other", "name": "Den", "floor": 2},
    ]
    data = _valid_property(rooms=rooms)
    base = _write_property(tmp_path, "test-house", data)
    items = build_checklist(load_property("test-house", properties_dir=base))
    by_id = {item.id: item for item in items}
    assert [item.key for item in items if item.room_id == "wc"] == [
        "coverage", "toilet", "toilet_paper", "sink", "hand_towel",
    ]
    assert by_id["wc.toilet"].min_count == 1
    assert by_id["wc.toilet"].sam_prompt == "toilet"
    assert by_id["wc.toilet_paper"].min_count == 1
    assert by_id["wc.hand_towel"].min_count == 1
    assert by_id["wc.hand_towel"].sam_prompt == "towel"
    assert [item.key for item in items if item.room_id == "dining"] == [
        "coverage", "table", "chairs", "floor_clear",
    ]
    assert [item.key for item in items if item.room_id == "wash"] == [
        "coverage", "machines", "floor_clear",
    ]
    assert [item.key for item in items if item.room_id == "study"] == [
        "coverage", "desk", "no_belongings", "floor_clear",
    ]
    assert [item.key for item in items if item.room_id == "hall"] == ["coverage", "floor_clear"]
    assert by_id["hall.floor_clear"].label == "Floors and stairs clear"
    assert [item.key for item in items if item.room_id == "den"] == [
        "coverage", "tidy", "floor_clear",
    ]


def test_beds_list_makes_one_item_per_bed(tmp_path):
    data = _valid_property(rooms=[
        {"id": "master", "type": "bedroom", "name": "Master", "floor": 2,
         "beds": ["king bed", "queen bed"]},
    ])
    base = _write_property(tmp_path, "test-house", data)
    items = {item.id: item for item in build_checklist(load_property("test-house", properties_dir=base))}
    assert items["master.bed_1"].label == "King bed made"
    assert items["master.bed_1"].requirement == "King bed made: sheets and blanket pulled up, pillows in place"
    assert items["master.bed_1"].min_count == 1
    assert items["master.bed_2"].label == "Queen bed made"
    assert "master.beds_made" not in items


def test_bad_beds_list_raises_clear_error(tmp_path):
    data = _valid_property(rooms=[
        {"id": "master", "type": "bedroom", "name": "Master", "floor": 2, "beds": ["king bed", ""]},
    ])
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="beds"):
        load_property("test-house", properties_dir=base)


def test_extra_item_replaces_template_item(tmp_path):
    data = _valid_property(extra_items=[
        {"key": "trash", "label": "Bins", "requirement": "Custom bins rule", "room": "kitchen"},
    ])
    base = _write_property(tmp_path, "test-house", data)
    items = build_checklist(load_property("test-house", properties_dir=base))
    trash = [item for item in items if item.id == "kitchen.trash"]
    assert len(trash) == 1
    assert trash[0].requirement == "Custom bins rule"


def test_duplicate_extra_items_rejected(tmp_path):
    data = _valid_property(extra_items=[
        {"key": "x", "label": "X", "requirement": "one", "room": "kitchen"},
        {"key": "x", "label": "X", "requirement": "two", "room": "kitchen"},
    ])
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="kitchen.x"):
        load_property("test-house", properties_dir=base)


def test_toilet_and_floor_requirements():
    from pot.rooms import TEMPLATES

    toilets = [spec for specs in TEMPLATES.values() for spec in specs if spec["key"] == "toilet"]
    assert toilets
    for spec in toilets:
        assert spec["requirement"] == "Toilet bowl, seat and base clean"
        assert "lid" not in spec["label"].lower()
    floors = [spec for specs in TEMPLATES.values() for spec in specs if spec["key"] == "floor_clear"]
    assert floors
    for spec in floors:
        assert spec["requirement"] == "Floor is clean: nothing left on it, no spots, spills or crumbs"


def test_extra_items_attach_to_room_or_house(tmp_path):
    data = _valid_property(
        extra_items=[
            {
                "key": "restock",
                "label": "Restock basket",
                "requirement": "Welcome basket is full",
                "room": "kitchen",
            },
            {"key": "alarm", "label": "Alarm set", "requirement": "Alarm is set on exit"},
        ]
    )
    base = _write_property(tmp_path, "test-house", data)
    items = _by_id(build_checklist(load_property("test-house", properties_dir=base)))
    assert items["kitchen.restock"].room_name == "Kitchen"
    assert items["house.alarm"].room_name == "Whole house"
