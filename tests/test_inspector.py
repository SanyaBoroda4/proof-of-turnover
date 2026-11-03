"""Offline tests for pot.inspector with a fake client."""

import base64

from pot.checklist import build_checklist
from pot.inspector import first_look
from pot.matcher import Assignment
from pot.meta_client import MetaClientError
from tests.fakes import FakeSpark, image_count, make_photos, photo_ids_in, tiny_property


def _assignments(pairs):
    return {
        pid: Assignment(photo_id=pid, room_id=room, confidence="high", seen="x")
        for pid, room in pairs
    }


def _checklist():
    return build_checklist(tiny_property())


def _item_enum(call):
    return call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]


def test_each_room_call_gets_own_photos_and_items(tmp_path):
    photos = make_photos(tmp_path / "photos", 4)
    assignments = _assignments([("P01", "bed"), ("P02", "bed"), ("P03", "bath"), ("P04", "unknown")])
    client = FakeSpark()
    look = first_look(client, photos, assignments, _checklist(), rooms={
        r["id"]: r for r in tiny_property()["rooms"]
    })
    assert len(client.calls) == 2
    by_purpose = {call["purpose"].split()[2]: call for call in client.calls}
    bed_call = by_purpose["bed"]
    assert photo_ids_in(bed_call["content"]) == ["P01", "P02", "P04"]
    assert set(_item_enum(bed_call)) == {
        "bed.beds_made", "bed.no_belongings", "bed.floor_clear",
    }
    bath_call = by_purpose["bath"]
    assert photo_ids_in(bath_call["content"]) == ["P03", "P04"]
    assert set(_item_enum(bath_call)) == {
        "bath.towels", "bath.toilet_paper", "bath.toilet", "bath.shower", "bath.sink",
        "bath.hand_towel", "bath.hand_soap",
    }
    assert "Bed" in bed_call["instructions"]
    assert "oak headboard" in bed_call["instructions"]
    assert all(c.status == "not_shown" for c in look.claims.values())
    assert look.notes == []
    assert ".coverage" not in " ".join(
        item_id for call in client.calls for item_id in _item_enum(call)
    )


def test_valid_claims_pass_through(tmp_path):
    photos = make_photos(tmp_path / "photos", 2)
    assignments = _assignments([("P01", "bed"), ("P02", "bath")])

    def good(call):
        enum = _item_enum(call)
        claims = []
        for item_id in enum:
            if item_id == "bed.beds_made":
                claims.append({
                    "item_id": item_id, "status": "done", "photo_id": "P01",
                    "seen": "made bed with pillows", "count_seen": 1,
                })
            elif item_id == "bath.towels":
                claims.append({
                    "item_id": item_id, "status": "problem", "photo_id": "P02",
                    "seen": "only one towel on the rack", "count_seen": 1,
                })
            else:
                claims.append({
                    "item_id": item_id, "status": "not_shown", "photo_id": "",
                    "seen": "", "count_seen": None,
                })
        return {"claims": claims, "other_issues": [
            {"photo_id": "P02", "description": "cracked mirror"},
        ]}

    look = first_look(FakeSpark(inspector=good), photos, assignments, _checklist())
    done = look.claims["bed.beds_made"]
    assert (done.status, done.photo_id, done.count_seen) == ("done", "P01", 1)
    assert done.seen == "made bed with pillows"
    problem = look.claims["bath.towels"]
    assert (problem.status, problem.photo_id) == ("problem", "P02")
    assert look.other_issues[0].description == "cracked mirror"
    # bath call issues carry the bath room id.
    assert {issue.room_id for issue in look.other_issues} <= {"bed", "bath"}


def test_bad_answers_become_not_shown(tmp_path):
    photos = make_photos(tmp_path / "photos", 2)
    assignments = _assignments([("P01", "bed"), ("P02", "bed")])

    def bad(call):
        if "bath" in call["purpose"]:
            return {"claims": [], "other_issues": []}
        return {
            "claims": [
                {"item_id": "bed.nope", "status": "done", "photo_id": "P01",
                 "seen": "x", "count_seen": None},
                {"item_id": "bed.beds_made", "status": "done", "photo_id": "",
                 "seen": "x", "count_seen": "three"},
                {"item_id": "bed.no_belongings", "status": "weird", "photo_id": "P01",
                 "seen": "x", "count_seen": None},
            ],
            "other_issues": [
                {"photo_id": "P99", "description": "ghost"},
                {"photo_id": "P01", "description": ""},
            ],
        }

    look = first_look(FakeSpark(inspector=bad), photos, assignments, _checklist())
    assert look.claims["bed.beds_made"].status == "not_shown"
    assert look.claims["bed.no_belongings"].status == "not_shown"
    assert look.claims["bed.floor_clear"].status == "not_shown"  # missing item
    assert look.claims["bath.towels"].status == "not_shown"  # empty claims list
    assert look.other_issues == []
    assert len(look.notes) >= 6


def test_count_forced_null_without_min_count(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    assignments = _assignments([("P01", "bed")])

    def counted(call):
        enum = _item_enum(call)
        return {
            "claims": [
                {"item_id": item_id, "status": "done", "photo_id": "P01",
                 "seen": "x", "count_seen": 5}
                for item_id in enum
            ],
            "other_issues": [],
        }

    look = first_look(FakeSpark(inspector=counted), photos, assignments, _checklist())
    assert look.claims["bed.no_belongings"].count_seen is None
    assert look.claims["bed.beds_made"].count_seen == 5


def test_not_shown_count_always_null_without_note(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    assignments = _assignments([("P01", "bed")])

    def counted(call):
        enum = _item_enum(call)
        return {
            "claims": [
                {"item_id": item_id, "status": "not_shown", "photo_id": "",
                 "seen": "", "count_seen": None}
                for item_id in enum
            ],
            "other_issues": [],
        }

    look = first_look(FakeSpark(inspector=counted), photos, assignments, _checklist())
    assert look.claims["bed.beds_made"].count_seen is None
    assert not any("count_seen" in note for note in look.notes)


def test_no_photos_no_calls():
    client = FakeSpark()
    look = first_look(client, [], {}, _checklist())
    assert client.calls == []
    assert look.claims
    assert all(c.status == "not_shown" for c in look.claims.values())
    assert look.notes


def test_house_items_get_extra_call_with_all_photos(tmp_path):
    photos = make_photos(tmp_path / "photos", 3)
    assignments = _assignments([("P01", "bed"), ("P02", "unknown"), ("P03", "bath")])
    prop = tiny_property()
    prop["extra_items"] = [
        {"key": "alarm", "label": "Alarm set", "requirement": "Alarm is set on exit"},
    ]
    checklist = build_checklist(prop)

    def inspector(call):
        enum = _item_enum(call)
        if "house.alarm" in enum:
            return {
                "claims": [
                    {"item_id": "house.alarm", "status": "done", "photo_id": "P02",
                     "seen": "alarm panel armed", "count_seen": None},
                ],
                "other_issues": [],
            }
        return {"claims": [
            {"item_id": i, "status": "not_shown", "photo_id": "", "seen": "", "count_seen": None}
            for i in enum
        ], "other_issues": []}

    client = FakeSpark(inspector=inspector)
    look = first_look(client, photos, assignments, checklist)
    house_calls = [c for c in client.calls if "house" in c["purpose"]]
    assert len(house_calls) == 1
    assert photo_ids_in(house_calls[0]["content"]) == ["P01", "P02", "P03"]
    assert image_count(house_calls[0]["content"]) == 3
    assert look.claims["house.alarm"].status == "done"


def test_house_items_keep_first_non_not_shown(tmp_path):
    photos = make_photos(tmp_path / "photos", 45)
    assignments = {p.id: Assignment(p.id, "unknown", "low", "") for p in photos}
    prop = tiny_property()
    prop["rooms"] = [prop["rooms"][0]]
    prop["extra_items"] = [
        {"key": "alarm", "label": "Alarm set", "requirement": "Alarm is set on exit"},
    ]
    checklist = [item for item in build_checklist(prop) if item.room_id == "house"]

    def inspector(call):
        if "batch 1/2" in call["purpose"]:
            return {"claims": [
                {"item_id": "house.alarm", "status": "not_shown", "photo_id": "",
                 "seen": "", "count_seen": None},
            ], "other_issues": []}
        return {"claims": [
            {"item_id": "house.alarm", "status": "done", "photo_id": "P41",
             "seen": "alarm panel armed", "count_seen": None},
        ], "other_issues": []}

    client = FakeSpark(inspector=inspector)
    look = first_look(client, photos, assignments, checklist)
    house_calls = [c for c in client.calls if "house" in c["purpose"]]
    assert [image_count(c["content"]) for c in house_calls] == [40, 5]
    assert look.claims["house.alarm"].status == "done"
    assert look.claims["house.alarm"].photo_id == "P41"


def _photo_enum(call):
    return call["schema"]["properties"]["claims"]["items"]["properties"]["photo_id"]["enum"]


def test_label_style_photo_id_kept_and_schema_has_enum(tmp_path):
    photos = make_photos(tmp_path / "photos", 2)
    assignments = _assignments([("P01", "bed"), ("P02", "bath")])

    def labelled(call):
        enum = _item_enum(call)
        return {
            "claims": [
                {
                    "item_id": item_id,
                    "status": "done",
                    "photo_id": "Photo P01",
                    "seen": "looks good",
                    "count_seen": None,
                }
                for item_id in enum
            ],
            "other_issues": [],
        }

    client = FakeSpark(inspector=labelled)
    look = first_look(client, photos, assignments, _checklist())
    assert look.claims["bed.beds_made"].status == "done"
    assert look.claims["bed.beds_made"].photo_id == "P01"
    bed_call = next(call for call in client.calls if call["purpose"].startswith("first look bed "))
    assert _photo_enum(bed_call) == ["P01", ""]


def test_claim_citing_unknown_photo_adds_note(tmp_path):
    photos = make_photos(tmp_path / "photos", 2)
    assignments = _assignments([("P01", "bed"), ("P02", "unknown")])

    def citing_unknown(call):
        enum = _item_enum(call)
        return {
            "claims": [
                {
                    "item_id": item_id,
                    "status": "done",
                    "photo_id": "P02",
                    "seen": "looks good",
                    "count_seen": None,
                }
                for item_id in enum
            ],
            "other_issues": [],
        }

    look = first_look(FakeSpark(inspector=citing_unknown), photos, assignments, _checklist())
    assert look.claims["bed.beds_made"].status == "done"
    assert any("bed.beds_made" in note and "P02" in note for note in look.notes)


def test_instructions_warn_about_other_rooms(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    assignments = _assignments([("P01", "bed")])
    client = FakeSpark()
    first_look(client, photos, assignments, _checklist())
    assert "other rooms" in client.calls[0]["instructions"]
    assert "never cite a photo of another room" in client.calls[0]["instructions"]


def test_duplicate_unknown_photo_issues_keep_first_in_room_order(tmp_path):
    photos = make_photos(tmp_path / "photos", 3)
    assignments = _assignments([("P01", "bed"), ("P02", "unknown"), ("P03", "bath")])

    def stained(call):
        enum = _item_enum(call)
        return {
            "claims": [
                {
                    "item_id": item_id, "status": "not_shown", "photo_id": "",
                    "seen": "", "count_seen": None,
                }
                for item_id in enum
            ],
            "other_issues": [{"photo_id": "P02", "description": "a stain"}],
        }

    look = first_look(FakeSpark(inspector=stained), photos, assignments, _checklist())
    assert len(look.other_issues) == 1
    assert look.other_issues[0].photo_id == "P02"
    assert look.other_issues[0].room_id == "bath"  # bath < bed in sorted order
    assert any("duplicate issue" in note for note in look.notes)


def test_failed_room_call_becomes_not_shown_and_run_continues(tmp_path):
    photos = make_photos(tmp_path / "photos", 2)
    assignments = _assignments([("P01", "bed"), ("P02", "bath")])

    def flaky(call):
        if call["purpose"].startswith("first look bed "):
            raise MetaClientError("first look bed: boom")
        enum = _item_enum(call)
        return {
            "claims": [
                {
                    "item_id": item_id, "status": "done", "photo_id": "P02",
                    "seen": "looks good", "count_seen": None,
                }
                for item_id in enum
            ],
            "other_issues": [],
        }

    look = first_look(FakeSpark(inspector=flaky), photos, assignments, _checklist())
    assert look.claims["bed.beds_made"].status == "not_shown"
    assert look.claims["bath.towels"].status == "done"
    assert any("check failed" in note for note in look.notes)


def test_first_look_sends_check_copies(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    assignments = _assignments([("P01", "bed")])
    client = FakeSpark()
    first_look(client, photos, assignments, _checklist())
    part = next(p for p in client.calls[0]["content"] if p["type"] == "input_image")
    sent = base64.b64decode(part["image_url"].split(",", 1)[1])
    assert sent == photos[0].check_path.read_bytes()
