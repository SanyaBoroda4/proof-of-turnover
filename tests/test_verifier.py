"""Offline tests for pot.verifier with a fake client."""

import base64

from pot.checklist import build_checklist
from pot.inspector import Claim
from pot.meta_client import MetaClientError
from pot.proof import PROBLEM, PROVEN, ProofItem
from pot.verifier import SamCheck, SecondLook, segment_problem_objects, verify
from tests.fakes import (
    SAM_FIXTURE,
    FakeSpark,
    image_count,
    make_photos,
    tiny_property,
)


def _checklist():
    return build_checklist(tiny_property())


def _claims(status="done", photo_id="P01"):
    return {
        item.id: Claim(item_id=item.id, status=status, photo_id=photo_id,
                       seen="x", count_seen=1)
        for item in _checklist() if item.key != "coverage"
    }


def test_second_look_gets_only_cited_check_photo(tmp_path):
    from pot.matcher import Assignment
    photos = make_photos(tmp_path / "photos", 2)
    claims = {"bed.beds_made": Claim("bed.beds_made", "done", "P02", "x", 1)}
    client = FakeSpark()
    result = verify(client, _checklist(), claims, photos,
                    assignments={"P02": Assignment("P02", "bed", "high", "x")})
    assert len(client.calls) == 1
    call = client.calls[0]
    assert image_count(call["content"]) == 1
    part = next(p for p in call["content"] if p["type"] == "input_image")
    sent = base64.b64decode(part["image_url"].split(",", 1)[1])
    assert sent == photos[1].check_path.read_bytes()
    assert "All 1 bed made" in call["instructions"]
    assert "Bed" in call["instructions"]
    assert call["effort"] == "medium"
    assert "bed.beds_made" in call["purpose"]
    second = result.second_looks["bed.beds_made"]
    assert (second.this_room, second.verdict, second.photo_id) == ("yes", "done", "P02")


def test_not_shown_claims_get_no_calls(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    claims = {"bed.beds_made": Claim("bed.beds_made", "not_shown", "", "", None)}
    client = FakeSpark()
    result = verify(client, _checklist(), claims, photos)
    assert client.calls == [] and client.sam_calls == []
    assert result.second_looks == {} and result.sams == {}


def test_bad_second_answers_become_unclear(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)

    def scripted(call):
        if "bed.beds_made" in call["purpose"]:
            return ["not a dict"]
        if "bath.towels" in call["purpose"]:
            return {"this_room": "maybe", "verdict": "done", "count_seen": 1, "reason": "x"}
        if "bath.toilet_paper" in call["purpose"]:
            return {"this_room": "yes", "verdict": "done", "count_seen": -2, "reason": "x"}
        return {"this_room": "yes", "verdict": "done", "count_seen": 2, "reason": "x"}

    claims = _claims()
    result = verify(FakeSpark(verifier=scripted), _checklist(), claims, photos)
    unclear = [s for s in result.second_looks.values() if s.verdict == "unclear"]
    assert len(unclear) == 2  # bad shape + bad enum
    assert any("bad answer" in note for note in result.notes)
    assert any("count_seen" in note for note in result.notes)


def test_sam_runs_only_for_sam_prompt_items(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    client = FakeSpark()
    result = verify(client, _checklist(), _claims(), photos)
    sam_ids = {call["purpose"].split()[1] for call in client.sam_calls}
    assert sam_ids == {
        "bed.beds_made", "bath.towels", "bath.toilet_paper", "bath.toilet",
    }
    assert all(call["prompt"] for call in client.sam_calls)
    assert result.sams["bed.beds_made"].count == 3  # fixture has 3 boxes
    assert len(result.sams["bed.beds_made"].boxes) == 3


def test_failed_calls_degrade_and_run_continues(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)

    def flaky_look(call):
        if "bed.beds_made" in call["purpose"]:
            raise MetaClientError("second look: boom")
        return {"this_room": "yes", "verdict": "done", "count_seen": 1, "reason": "ok"}

    def flaky_sam(call):
        raise MetaClientError("sam: boom")

    client = FakeSpark(verifier=flaky_look, sam=flaky_sam)
    result = verify(client, _checklist(), _claims(), photos)
    failed = result.second_looks["bed.beds_made"]
    assert (failed.this_room, failed.verdict) == ("unclear", "unclear")
    assert result.second_looks["bath.towels"].verdict == "done"
    assert result.sams["bed.beds_made"].count == 0
    assert result.sams["bed.beds_made"].boxes == []
    assert sum("check failed" in note for note in result.notes) == 1 + 4


def test_room_decision_asks_citing_rooms_only(tmp_path):
    from pot.matcher import Assignment
    photos = make_photos(tmp_path / "photos", 1)
    claims = {
        "bed.beds_made": Claim("bed.beds_made", "done", "P01", "x", 1),
        "bath.towels": Claim("bath.towels", "problem", "P01", "y", 1),
    }
    assignments = {"P01": Assignment("P01", "unknown", "low", "")}
    client = FakeSpark()
    result = verify(client, _checklist(), claims, photos,
                    rooms={r["id"]: r for r in tiny_property()["rooms"]},
                    assignments=assignments)
    assert result.room_decisions == {"P01": "bath"}  # default fake picks first candidate
    decision_calls = [c for c in client.calls if c["purpose"].startswith("room decision")]
    assert len(decision_calls) == 1
    assert image_count(decision_calls[0]["content"]) == 1
    assert decision_calls[0]["schema"]["properties"]["room_id"]["enum"] == ["bath", "bed", "none"]
    assert "Bed" in decision_calls[0]["instructions"]
    assert "oak headboard" in decision_calls[0]["instructions"]


def test_room_decision_bad_answer_and_failure_become_none(tmp_path):
    from pot.matcher import Assignment
    photos = make_photos(tmp_path / "photos", 2)
    claims = {"bed.beds_made": Claim("bed.beds_made", "done", "P01", "x", 1)}

    def bad(call):
        return {"room_id": "attic"}

    assignments = {"P01": Assignment("P01", "unknown", "low", "")}
    result = verify(FakeSpark(decider=bad), _checklist(), claims, photos, assignments=assignments)
    assert result.room_decisions == {"P01": "none"}
    assert any("bad answer" in note for note in result.notes)

    def boom(call):
        raise MetaClientError("decide: boom")

    result = verify(FakeSpark(decider=boom), _checklist(), claims, photos, assignments=assignments)
    assert result.room_decisions == {"P01": "none"}
    assert any("check failed" in note for note in result.notes)


def test_segment_problem_objects_uses_problem_object(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    proofs = {
        "bed.beds_made": ProofItem("bed.beds_made", PROBLEM, "stained", "P01"),
        "bath.towels": ProofItem("bath.towels", PROVEN, "ok", "P01"),
        "bath.sink": ProofItem("bath.sink", PROBLEM, "x", "P01"),
    }
    seconds = {
        "bed.beds_made": SecondLook("bed.beds_made", "P01", "yes", "not_done", None,
                                    "stained", "brown stain"),
        "bath.sink": SecondLook("bath.sink", "P01", "yes", "not_done", None, "dirty", ""),
    }
    client = FakeSpark()
    found, notes = segment_problem_objects(client, proofs, seconds, {"P01": photos[0]})
    assert set(found) == {"bed.beds_made"}  # proven skipped, empty object skipped
    assert found["bed.beds_made"].prompt == "brown stain"
    assert found["bed.beds_made"].count == 3
    assert len(client.sam_calls) == 1
    picks = [c for c in client.calls if c["purpose"].startswith("pick problem boxes")]
    assert len(picks) == 1  # the 3-box result goes through one pick call
    assert notes == ["Box pick for bed.beds_made: kept 3 of 3"]


def test_verify_effort_env_override(tmp_path, monkeypatch):
    from pot.matcher import Assignment
    photos = make_photos(tmp_path / "photos", 1)
    claims = {"bed.beds_made": Claim("bed.beds_made", "done", "P01", "x", 1)}
    assignments = {"P01": Assignment("P01", "bed", "high", "x")}
    monkeypatch.setenv("POT_VERIFY_EFFORT", "low")
    client = FakeSpark()
    verify(client, _checklist(), claims, photos, assignments=assignments)
    assert client.calls[0]["effort"] == "low"


def test_sam_unreadable_output_counts_as_none(tmp_path, monkeypatch):
    import pot.verifier as verifier_mod

    def boom(*args, **kwargs):
        raise ValueError("bad mask")

    monkeypatch.setattr(verifier_mod, "detect", boom)
    photos = make_photos(tmp_path / "photos", 1)
    client = FakeSpark()
    result = verify(client, _checklist(), _claims(), photos)
    assert result.sams["bed.beds_made"].count == 0
    assert result.sams["bed.beds_made"].boxes == []
    assert any("unreadable" in note for note in result.notes)


def _numbered_boxes(count):
    return [
        {"left": i * 2, "top": 1, "right": i * 2 + 4, "bottom": 8, "confidence": 0.9}
        for i in range(count)
    ]


def test_pick_problem_boxes_keeps_only_picked(tmp_path):
    from pot.verifier import pick_problem_boxes

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.beds_made", "P01", "brown stain", 3, _numbered_boxes(3))
    client = FakeSpark(picker=lambda call: {"boxes": [3, 1, 3, 9]})
    kept, notes = pick_problem_boxes(client, "bed.beds_made", photos[0], sam, "brown stain")
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["purpose"].startswith("pick problem boxes")
    assert "brown stain" in call["instructions"]
    assert image_count(call["content"]) == 1
    assert [box["left"] for box in kept.boxes] == [0, 4]  # boxes 1 and 3
    assert kept.count == 2
    assert notes == ["Box pick for bed.beds_made: kept 2 of 3"]


def test_pick_problem_boxes_none_keeps_no_box(tmp_path):
    from pot.verifier import pick_problem_boxes

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.beds_made", "P01", "brown stain", 2, _numbered_boxes(2))
    client = FakeSpark(picker=lambda call: {"boxes": []})
    kept, notes = pick_problem_boxes(client, "bed.beds_made", photos[0], sam, "brown stain")
    assert kept.count == 0 and kept.boxes == []
    assert notes == ["Box pick for bed.beds_made: kept 0 of 2"]


def test_pick_problem_boxes_single_box_makes_no_call(tmp_path):
    from pot.verifier import pick_problem_boxes

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.beds_made", "P01", "brown stain", 1, _numbered_boxes(1))
    client = FakeSpark()
    kept, notes = pick_problem_boxes(client, "bed.beds_made", photos[0], sam, "brown stain")
    assert kept is sam and notes == []
    assert client.calls == []


def test_pick_problem_boxes_failure_keeps_all(tmp_path):
    from pot.verifier import pick_problem_boxes

    def boom(call):
        raise MetaClientError("pick: boom")

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.beds_made", "P01", "brown stain", 3, _numbered_boxes(3))
    kept, notes = pick_problem_boxes(
        FakeSpark(picker=boom), "bed.beds_made", photos[0], sam, "brown stain"
    )
    assert kept is sam
    assert notes == ["Box pick for bed.beds_made failed (pick: boom); kept all 3"]


def test_refine_problem_boxes_single_box_makes_no_call(tmp_path):
    from pot.verifier import refine_problem_boxes

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.beds_made", "P01", "stain", 1, _numbered_boxes(1))
    client = FakeSpark()
    kept, notes = refine_problem_boxes(
        client, "bed.beds_made", photos[0], sam, "stain")
    assert kept is sam and notes == []
    assert client.calls == []


def test_refine_problem_boxes_eight_boxes_trigger_one_pick(tmp_path):
    from pot.verifier import refine_problem_boxes

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.beds_made", "P01", "shoe", 8, _numbered_boxes(8))
    client = FakeSpark(picker=lambda call: {"boxes": [2]})
    kept, notes = refine_problem_boxes(
        client, "bed.beds_made", photos[0], sam, "shoe")
    assert len(client.calls) == 1
    assert kept.count == 1
    assert notes == ["Box pick for bed.beds_made: kept 1 of 8"]


def test_refine_problem_boxes_many_boxes_draw_one_area_box(tmp_path):
    from pot.verifier import refine_problem_boxes

    photos = make_photos(tmp_path / "photos", 1)
    boxes = [
        {"left": 0, "top": 0, "right": 4, "bottom": 4, "confidence": 0.6},
        {"left": 10, "top": 2, "right": 14, "bottom": 8, "confidence": 0.9},
    ] + _numbered_boxes(7)
    sam = SamCheck("bed.beds_made", "P01", "jar", 9, boxes)
    client = FakeSpark()
    kept, notes = refine_problem_boxes(
        client, "bed.beds_made", photos[0], sam, "jar")
    assert client.calls == []
    assert kept.count == 1
    area = kept.boxes[0]
    assert (area["left"], area["top"], area["right"], area["bottom"]) == (0, 0, 16, 8)
    assert area["confidence"] == 0.9
    assert notes == ["Box pick for bed.beds_made skipped (9 boxes): drew 1 area box"]


def _area_item():
    from pot.checklist import ChecklistItem

    return ChecklistItem(
        id="bed.fridge", room_id="bed", room_name="Bed", floor=1, key="fridge",
        label="Fridge", requirement="Fridge doors open for the photo: shelves empty.",
        sam_prompt=None, min_count=None, critical=True,
        problem_area="refrigerator")


def test_refine_problem_area_picks_outlining_boxes(tmp_path):
    from pot.verifier import refine_problem_area

    photos = make_photos(tmp_path / "photos", 1)
    sam = SamCheck("bed.fridge", "P01", "refrigerator", 3, _numbered_boxes(3))
    client = FakeSpark(picker=lambda call: {"boxes": [2]})
    kept, notes = refine_problem_area(
        client, _area_item(), photos[0], sam, "jar")
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["purpose"] == "pick problem area bed.fridge P01"
    assert 'candidate outlines of "refrigerator"' in call["instructions"]
    assert "Fridge doors open for the photo" in call["instructions"]
    assert kept.count == 1 and kept.prompt == "refrigerator"
    assert notes == ["Box pick for bed.fridge: kept 1 of 3"]


def test_refine_problem_area_zero_boxes_falls_back_to_object(tmp_path):
    from pot.verifier import refine_problem_area

    def sam_by_prompt(call):
        return "" if call["prompt"] == "refrigerator" else SAM_FIXTURE

    photos = make_photos(tmp_path / "photos", 1)
    empty = SamCheck("bed.fridge", "P01", "refrigerator", 0, [])
    client = FakeSpark(sam=sam_by_prompt)
    kept, notes = refine_problem_area(
        client, _area_item(), photos[0], empty, "jar")
    assert kept.count == 3  # the object path found the jars
    assert kept.prompt == "jar"
    assert any("found nothing" in note for note in notes)


def test_refine_problem_area_zero_boxes_and_no_object(tmp_path):
    from pot.verifier import refine_problem_area

    photos = make_photos(tmp_path / "photos", 1)
    empty = SamCheck("bed.fridge", "P01", "refrigerator", 0, [])
    client = FakeSpark()
    kept, notes = refine_problem_area(client, _area_item(), photos[0], empty, "")
    assert kept.count == 0 and notes == []
    assert client.calls == [] and client.sam_calls == []


def test_segment_problem_area_starts_without_object(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    proofs = {
        "bed.fridge": ProofItem("bed.fridge", PROBLEM, "full", "P01"),
        "bed.beds_made": ProofItem("bed.beds_made", PROBLEM, "messy", "P01"),
    }
    seconds = {
        "bed.fridge": SecondLook("bed.fridge", "P01", "yes", "not_done", None,
                                 "full", ""),
        "bed.beds_made": SecondLook("bed.beds_made", "P01", "yes", "not_done",
                                    None, "messy", "dirty pile"),
    }
    items_by_id = {"bed.fridge": _area_item()}
    client = FakeSpark()
    found, notes = segment_problem_objects(
        client, proofs, seconds, {"P01": photos[0]}, items_by_id=items_by_id)
    assert set(found) == {"bed.fridge", "bed.beds_made"}
    assert found["bed.fridge"].prompt == "refrigerator"  # started with no object
    purposes = [c["purpose"] for c in client.sam_calls]
    assert any(p.startswith("sam problem area bed.fridge") for p in purposes)
    assert any(p.startswith("sam problem bed.beds_made") for p in purposes)
    picks = [c["purpose"] for c in client.calls]
    assert any(p.startswith("pick problem area bed.fridge") for p in picks)
    assert any(p.startswith("pick problem boxes bed.beds_made") for p in picks)
