"""Offline tests for property loading and checklist generation."""

import json

import pytest

from pot.checklist import PropertyError, build_checklist, load_property


def _by_id(items):
    return {item.id: item for item in items}


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


def test_unknown_room_type_raises_clear_error(tmp_path):
    data = _valid_property()
    data["rooms"][0]["type"] = "garage"
    base = _write_property(tmp_path, "test-house", data)
    with pytest.raises(PropertyError, match="type"):
        load_property("test-house", properties_dir=base)


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
