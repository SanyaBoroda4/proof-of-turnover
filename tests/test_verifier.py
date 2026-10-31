"""Offline tests for pot.verifier with a fake client."""

import base64

from pot.checklist import build_checklist
from pot.inspector import Claim
from pot.meta_client import MetaClientError
from pot.proof import PROBLEM, PROVEN, ProofItem
from pot.verifier import SamCheck, SecondLook, segment_problem_objects, verify
from tests.fakes import FakeSpark, image_count, make_photos, tiny_property


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
