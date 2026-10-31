"""Tests for pot.tour: generation, validation and fixups (no network)."""

import json

import pytest

from pot.checklist import PropertyError, build_checklist
from pot.tour import fixup_tour, generate_tour, load_tour, save_tour
from tests.fakes import FakeSpark, tiny_property


def _shot(shot_id, room_id, item_ids):
    return {
        "shot_id": shot_id,
        "room_id": room_id,
        "item_ids": list(item_ids),
        "instruction_es": f"Foto {shot_id}",
        "instruction_en": f"Photo {shot_id}",
    }


def test_fixup_drops_unknown_rooms_items_and_dupes():
    property = tiny_property()
    checklist = build_checklist(property)
    bed_item = next(i.id for i in checklist if i.room_id == "bed" and i.key != "coverage")
    shots, notes = fixup_tour(property, checklist, [
        _shot("s1", "bed", [bed_item, "bed.nope"]),
        _shot("s1", "bed", [bed_item]),
        _shot("s2", "attic", [bed_item]),
        "not a shot",
    ])
    plain = [s for s in shots if "-extra-" not in s["shot_id"]]
    assert [s["shot_id"] for s in plain] == ["s1"]
    assert plain[0]["item_ids"] == [bed_item]
    assert any("unknown item" in n for n in notes)
    assert any("duplicate" in n for n in notes)
    assert any("unknown room" in n for n in notes)
    assert any("malformed" in n for n in notes)


def test_fixup_adds_missed_critical_item_at_end_of_its_room():
    property = tiny_property()
    checklist = build_checklist(property)
    bed_items = [i.id for i in checklist if i.room_id == "bed" and i.key != "coverage"]
    bath_items = [i.id for i in checklist if i.room_id == "bath" and i.key != "coverage"]
    missed = bath_items[-1]
    shots, notes = fixup_tour(property, checklist, [
        _shot("bed-1", "bed", bed_items),
        _shot("bath-1", "bath", bath_items[:-1]),
    ])
    extra = [s for s in shots if s["shot_id"].endswith("-extra-1")]
    assert len(extra) == 1
    assert extra[0]["room_id"] == "bath"
    assert extra[0]["item_ids"] == [missed]
    order = [s["shot_id"] for s in shots]
    assert order.index(extra[0]["shot_id"]) == order.index("bath-1") + 1
    assert any(missed in n for n in notes)


def test_fixup_strips_coverage_and_keeps_clean_tour():
    property = tiny_property()
    checklist = build_checklist(property)
    bed_items = ["bed.coverage"] + [
        i.id for i in checklist if i.room_id == "bed" and i.key != "coverage"
    ]
    bath_items = [i.id for i in checklist if i.room_id == "bath" and i.key != "coverage"]
    shots, notes = fixup_tour(property, checklist, [
        _shot("bed-1", "bed", bed_items),
        _shot("bath-1", "bath", bath_items),
    ])
    assert notes == []
    assert all("coverage" not in ",".join(s["item_ids"]) for s in shots)


def test_generate_tour_saves_and_validates(tmp_path):
    property = tiny_property()
    checklist = build_checklist(property)
    bed_items = [i.id for i in checklist if i.room_id == "bed" and i.key != "coverage"]
    answer = {"shots": [_shot("bed-1", "bed", bed_items)]}

    def tour(call):
        assert "bed" in call["instructions"] and "bath" in call["instructions"]
        return answer

    client = FakeSpark(tour=tour)
    shots, _notes = generate_tour(client, property, checklist, properties_dir=tmp_path)
    saved = json.loads((tmp_path / "tiny" / "tour.json").read_text(encoding="utf-8"))
    assert saved["shots"] == shots
    assert load_tour("tiny", tmp_path) == shots
    # The bath items were missed, so extras were appended for them.
    covered = {i for s in shots for i in s["item_ids"]}
    critical = {i.id for i in checklist if i.critical and i.key != "coverage"}
    assert critical <= covered


def test_generate_tour_empty_answer_builds_fallback(tmp_path):
    property = tiny_property()
    checklist = build_checklist(property)
    shots, _notes = generate_tour(FakeSpark(), property, checklist,
                                  properties_dir=tmp_path)
    covered = {i for s in shots for i in s["item_ids"]}
    critical = {i.id for i in checklist if i.critical and i.key != "coverage"}
    assert critical <= covered
    assert all("-extra-" in s["shot_id"] for s in shots)


def test_generate_tour_malformed_answer_raises(tmp_path):
    property = tiny_property()
    client = FakeSpark(tour=lambda call: {"nope": []})
    with pytest.raises(PropertyError):
        generate_tour(client, property, build_checklist(property),
                      properties_dir=tmp_path)


def test_load_tour_missing_raises(tmp_path):
    with pytest.raises(PropertyError):
        load_tour("tiny", tmp_path)


def test_save_and_load_roundtrip(tmp_path):
    shots = [_shot("s1", "bed", ["bed.beds_made"])]
    save_tour("tiny", shots, tmp_path)
    assert load_tour("tiny", tmp_path) == shots


def test_fixup_keeps_spanish_room_names():
    property = tiny_property()
    checklist = build_checklist(property)
    given = [_shot("s1", "bed", ["bed.beds_made"])]
    given[0]["room_name_es"] = "Dormitorio"
    shots, _notes = fixup_tour(property, checklist, given)
    assert shots[0]["room_name_es"] == "Dormitorio"
    extras = [s for s in shots if "-extra-" in s["shot_id"]]
    assert extras and all(s["room_name_es"] for s in extras)


def test_fixup_keeps_short_es_with_room_fallback():
    from pot.tour import tour_schema

    assert "short_es" in tour_schema()["properties"]["shots"]["items"]["required"]
    property = tiny_property()
    checklist = build_checklist(property)
    given = [_shot("s1", "bed", ["bed.beds_made"])]
    given[0]["room_name_es"] = "Dormitorio"
    given[0]["short_es"] = "cama"
    shots, _notes = fixup_tour(property, checklist, given)
    assert shots[0]["short_es"] == "cama"
    bare = [_shot("s1", "bed", ["bed.beds_made"])]
    shots, _notes = fixup_tour(property, checklist, bare)
    assert shots[0]["short_es"] == shots[0]["room_name_es"]


def test_tour_instructions_cover_open_doors_and_short_names():
    from pot import prompts

    property = tiny_property()
    text = prompts.tour_instructions(property, build_checklist(property), False)
    assert "door or lid must be open" in text
    assert "opened only for the photo" in text
    assert "short_es" in text
    assert "2-4 Spanish words" in text


def test_generate_tour_records_model_and_time(tmp_path):
    property = tiny_property()
    checklist = build_checklist(property)
    bed_items = [i.id for i in checklist if i.room_id == "bed" and i.key != "coverage"]
    shot = _shot("bed-1", "bed", bed_items)
    shot["room_name_es"] = "Dormitorio"
    client = FakeSpark(tour=lambda call: {"shots": [shot]})
    shots, _notes = generate_tour(client, property, checklist, properties_dir=tmp_path)
    saved = json.loads((tmp_path / "tiny" / "tour.json").read_text(encoding="utf-8"))
    assert saved["model"] == "unknown"
    assert saved["generated_at"]
    assert saved["shots"][0]["room_name_es"] == "Dormitorio"


def test_cli_writes_tour(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    import pot.tour as tour_mod

    props = tmp_path / "properties" / "tiny"
    props.mkdir(parents=True)
    definition = json.loads(Path("properties/demo-house/property.json").read_text(encoding="utf-8"))
    definition["id"] = "tiny"
    (props / "property.json").write_text(json.dumps(definition), encoding="utf-8")
    monkeypatch.setattr("pot.checklist.DEFAULT_PROPERTIES_DIR", tmp_path / "properties")
    monkeypatch.setattr("pot.tour.DEFAULT_PROPERTIES_DIR", tmp_path / "properties")
    monkeypatch.setattr(tour_mod, "MetaClient", lambda *a, **k: FakeSpark(
        tour=lambda call: {"shots": []}))
    assert tour_mod.main(["pot.tour", "tiny"]) == 0
    out = capsys.readouterr().out
    assert "shots" in out
    assert (props / "tour.json").is_file()
    assert tour_mod.main(["pot.tour", "nope"]) == 1
