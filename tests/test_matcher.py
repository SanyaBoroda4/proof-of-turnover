"""Offline tests for pot.matcher with a fake client."""

from pot.checklist import build_checklist
from pot.matcher import match_rooms
from pot.meta_client import MetaClientError
from tests.fakes import FakeSpark, image_count, make_photos, photo_ids_in, tiny_property


def _assigned_rooms(call):
    rooms = list(call["schema"]["properties"]["matches"]["items"]["properties"]["room_id"]["enum"])
    return {
        "matches": [
            {"photo_id": pid, "room_id": rooms[0], "confidence": "high", "seen": "a room"}
            for pid in photo_ids_in(call["content"])
        ]
    }


def test_batches_at_40_and_one_room_per_photo(tmp_path):
    photos = make_photos(tmp_path / "photos", 45)
    client = FakeSpark(matcher=_assigned_rooms)
    result = match_rooms(client, photos, tiny_property())
    assert len(client.calls) == 2
    assert [image_count(call["content"]) for call in client.calls] == [40, 5]
    assert sorted(result.assignments) == [p.id for p in photos]
    assert len(result.assignments) == 45
    assert "bed" in client.calls[0]["instructions"]
    assert "oak headboard" in client.calls[0]["instructions"]


def test_bad_answers_become_unknown(tmp_path):
    photos = make_photos(tmp_path / "photos", 3)

    def bad(call):
        return {
            "matches": [
                {"photo_id": "P01", "room_id": "nope", "confidence": "high", "seen": "x"},
                {"photo_id": "P03", "room_id": "bed", "confidence": "huge", "seen": "x"},
            ]
        }

    result = match_rooms(FakeSpark(matcher=bad), photos, tiny_property())
    assert result.assignments["P01"].room_id == "unknown"
    assert result.assignments["P02"].room_id == "unknown"
    assert result.assignments["P03"].room_id == "bed"
    assert result.assignments["P03"].confidence == "low"
    assert len(result.notes) == 3
    assert result.coverage == {"bed": "needs proof", "bath": "needs proof"}


def test_coverage_needs_high_or_medium(tmp_path):
    photos = make_photos(tmp_path / "photos", 2)

    def mixed(call):
        return {
            "matches": [
                {"photo_id": "P01", "room_id": "bed", "confidence": "high", "seen": "a bed"},
                {"photo_id": "P02", "room_id": "bath", "confidence": "low", "seen": "maybe bath"},
            ]
        }

    result = match_rooms(FakeSpark(matcher=mixed), photos, tiny_property())
    assert result.coverage == {"bed": "covered", "bath": "needs proof"}
    # Medium counts as covered too.
    def medium(call):
        return {
            "matches": [
                {"photo_id": "P01", "room_id": "bed", "confidence": "medium", "seen": "a bed"},
                {"photo_id": "P02", "room_id": "bath", "confidence": "high", "seen": "a bath"},
            ]
        }

    result = match_rooms(FakeSpark(matcher=medium), photos, tiny_property())
    assert result.coverage == {"bed": "covered", "bath": "covered"}


def test_no_photos_no_calls():
    client = FakeSpark()
    result = match_rooms(client, [], tiny_property())
    assert client.calls == []
    assert result.assignments == {}
    assert result.coverage == {"bed": "needs proof", "bath": "needs proof"}
    assert result.notes


def test_label_style_photo_ids_are_normalized(tmp_path):
    photos = make_photos(tmp_path / "photos", 3)

    def labelled(call):
        return {
            "matches": [
                {"photo_id": "Photo P01", "room_id": "bed", "confidence": "high", "seen": "a bed"},
                {"photo_id": "p02", "room_id": "bath", "confidence": "high", "seen": "a bath"},
                {"photo_id": " P03 ", "room_id": "unknown", "confidence": "low", "seen": ""},
            ]
        }

    result = match_rooms(FakeSpark(matcher=labelled), photos, tiny_property())
    assert result.assignments["P01"].room_id == "bed"
    assert result.assignments["P02"].room_id == "bath"
    assert result.assignments["P03"].room_id == "unknown"
    assert result.coverage == {"bed": "covered", "bath": "covered"}


def test_failed_batch_becomes_unknown_and_run_continues(tmp_path):
    photos = make_photos(tmp_path / "photos", 45)

    def flaky(call):
        if "batch 1/2" in call["purpose"]:
            raise MetaClientError("match rooms: boom")
        return {
            "matches": [
                {"photo_id": pid, "room_id": "bed", "confidence": "high", "seen": "a bed"}
                for pid in photo_ids_in(call["content"])
            ]
        }

    result = match_rooms(FakeSpark(matcher=flaky), photos, tiny_property())
    assert len(result.assignments) == 45
    assert all(a.room_id == "unknown" for pid, a in result.assignments.items() if pid < "P41")
    assert all(a.room_id == "bed" for pid, a in result.assignments.items() if pid >= "P41")
    assert any("check failed" in note for note in result.notes)
    assert result.coverage["bed"] == "covered"
