"""Offline tests for pot.verifier with a fake client."""

import base64

from pot.checklist import build_checklist
from pot.inspector import Claim
from pot.meta_client import MetaClientError
from pot.verifier import SamCheck, SecondLook, verify
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
    photos = make_photos(tmp_path / "photos", 2)
    claims = {"bed.beds_made": Claim("bed.beds_made", "done", "P02", "x", 1)}
    client = FakeSpark()
    result = verify(client, _checklist(), claims, photos)
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
