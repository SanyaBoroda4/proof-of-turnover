"""Offline tests for pot.proof: rule table, verdict, pruning, evidence."""

from pathlib import Path

from PIL import Image

from pot.checklist import ChecklistItem
from pot.inspector import Claim, OtherIssue
from pot.matcher import Assignment
from pot.proof import (
    NEEDS_PROOF,
    PROBLEM,
    PROVEN,
    prune_other_issues,
    prove_all,
    prove_item,
    save_evidence,
    verdict,
)
from pot.verifier import SamCheck, SecondLook
from tests.fakes import make_photos


def _item(**overrides):
    data = {
        "id": "bed.beds_made", "room_id": "bed", "room_name": "Bed", "floor": 1,
        "key": "beds_made", "label": "Beds made", "requirement": "All beds made",
        "sam_prompt": "bed", "min_count": 1, "critical": True,
    }
    data.update(overrides)
    return ChecklistItem(**data)


def _claim(status="done", photo_id="P01"):
    return Claim(item_id="bed.beds_made", status=status, photo_id=photo_id,
                 seen="a made bed", count_seen=1)


def _second(this_room="yes", verdict="done", count_seen=1):
    return SecondLook(item_id="bed.beds_made", photo_id="P01", this_room=this_room,
                      verdict=verdict, count_seen=count_seen, reason="looks made")


def _box(left=5, top=5, right=15, bottom=15, confidence=0.9):
    return {"left": left, "top": top, "right": right, "bottom": bottom, "confidence": confidence}


def _sam(count=1):
    return SamCheck(item_id="bed.beds_made", photo_id="P01", prompt="bed",
                    count=count, boxes=[_box(left=5 + i) for i in range(count)])


_DEFAULT = object()


def _prove(item=_DEFAULT, claim=_DEFAULT, second=_DEFAULT, sam=_DEFAULT, unknown=False,
           coverage="covered", first_photo="P01"):
    item = _item() if item is _DEFAULT else item
    claim = _claim() if claim is _DEFAULT else claim
    second = _second() if second is _DEFAULT else second
    sam = _sam() if sam is _DEFAULT else sam
    return prove_item(item, claim, second, sam, unknown, coverage, first_photo)


def test_not_shown_needs_proof():
    proof = _prove(claim=_claim(status="not_shown", photo_id=""))
    assert (proof.status, proof.photo_id) == (NEEDS_PROOF, "")
    assert "No photo shows it" in proof.reason


def test_missing_claim_needs_proof():
    proof = _prove(claim=None)
    assert proof.status == NEEDS_PROOF


def test_missing_second_look_needs_proof():
    proof = _prove(second=None)
    assert proof.status == NEEDS_PROOF


def test_done_all_confirming_is_proven():
    proof = _prove()
    assert proof.status == PROVEN
    assert proof.photo_id == "P01"


def test_done_without_sam_or_count_is_proven():
    item = _item(sam_prompt=None, min_count=None)
    proof = _prove(item=item, sam=None)
    assert proof.status == PROVEN


def test_done_photo_of_another_room_needs_proof():
    proof = _prove(second=_second(this_room="no"))
    assert proof.status == NEEDS_PROOF
    assert "another room" in proof.reason


def test_done_unknown_photo_needs_yes():
    assert _prove(unknown=True, second=_second(this_room="unclear")).status == NEEDS_PROOF
    assert _prove(unknown=True, second=_second(this_room="yes")).status == PROVEN


def test_done_room_matched_photo_tolerates_unclear_room():
    assert _prove(unknown=False, second=_second(this_room="unclear")).status == PROVEN


def test_done_second_not_done_or_unclear_needs_proof():
    assert _prove(second=_second(verdict="not_done")).status == NEEDS_PROOF
    assert _prove(second=_second(verdict="unclear")).status == NEEDS_PROOF


def test_done_sam_finds_nothing_needs_proof():
    proof = _prove(sam=_sam(count=0))
    assert proof.status == NEEDS_PROOF
    assert "bed" in proof.reason


def test_done_sam_missing_needs_proof():
    assert _prove(sam=None).status == NEEDS_PROOF


def test_done_count_reached_by_second_look_when_sam_merges():
    item = _item(min_count=4)
    proof = _prove(item=item, sam=_sam(count=1), second=_second(count_seen=4))
    assert proof.status == PROVEN


def test_done_count_reached_by_sam():
    item = _item(min_count=2)
    proof = _prove(item=item, sam=_sam(count=3), second=_second(count_seen=None))
    assert proof.status == PROVEN


def test_done_count_too_low_needs_proof():
    item = _item(min_count=4)
    proof = _prove(item=item, sam=_sam(count=1), second=_second(count_seen=2))
    assert proof.status == NEEDS_PROOF
    assert "2 of at least 4" in proof.reason


def test_done_count_without_sam_uses_second_look():
    item = _item(sam_prompt=None, min_count=2)
    assert _prove(item=item, sam=None, second=_second(count_seen=2)).status == PROVEN
    assert _prove(item=item, sam=None, second=_second(count_seen=1)).status == NEEDS_PROOF


def test_problem_confirmed_stays_problem():
    proof = _prove(claim=_claim(status="problem"), second=_second(verdict="not_done"))
    assert proof.status == PROBLEM
    assert proof.reason == "looks made"


def test_problem_second_done_or_unclear_disagrees():
    assert _prove(claim=_claim(status="problem"), second=_second(verdict="done")).status == NEEDS_PROOF
    proof = _prove(claim=_claim(status="problem"), second=_second(verdict="unclear"))
    assert proof.status == NEEDS_PROOF
    assert "disagree" in proof.reason


def test_problem_wrong_room_disagrees():
    proof = _prove(claim=_claim(status="problem"), second=_second(verdict="not_done", this_room="no"))
    assert proof.status == NEEDS_PROOF


def test_problem_unknown_photo_needs_yes():
    proof = _prove(claim=_claim(status="problem"),
                   second=_second(verdict="not_done", this_room="unclear"),
                   unknown=True)
    assert proof.status == NEEDS_PROOF
    proof = _prove(claim=_claim(status="problem"),
                   second=_second(verdict="not_done", this_room="yes"),
                   unknown=True)
    assert proof.status == PROBLEM


def test_coverage_covered_is_proven_with_first_photo():
    item = _item(id="bed.coverage", key="coverage", sam_prompt=None, min_count=None)
    proof = _prove(item=item, claim=None, second=None, sam=None,
                   coverage="covered", first_photo="P02")
    assert (proof.status, proof.photo_id) == (PROVEN, "P02")


def test_coverage_missing_needs_proof():
    item = _item(id="bed.coverage", key="coverage", sam_prompt=None, min_count=None)
    proof = _prove(item=item, claim=None, second=None, sam=None, coverage="needs proof")
    assert proof.status == NEEDS_PROOF
    assert "No photo of this room" in proof.reason


def test_prove_all_wires_unknown_and_first_photo():
    bed = _item()
    coverage = _item(id="bed.coverage", key="coverage", sam_prompt=None, min_count=None)
    claims = {"bed.beds_made": _claim(status="done", photo_id="P09")}
    seconds = {"bed.beds_made": _second()}
    sams = {"bed.beds_made": _sam()}
    assignments = {
        "P01": Assignment("P01", "bed", "high", "x"),
        "P09": Assignment("P09", "unknown", "low", "x"),
    }
    proofs = prove_all([bed, coverage], claims, seconds, sams, assignments,
                       {"bed": "covered"}, ["P01", "P09"])
    assert proofs["bed.beds_made"].status == PROVEN  # unknown photo + this_room yes
    assert proofs["bed.coverage"].photo_id == "P01"  # first matched photo cited


def test_prune_drops_issues_on_problem_photos():
    claims = {"bed.beds_made": _claim(status="problem", photo_id="P01")}
    proofs = {"bed.beds_made": _prove(claim=_claim(status="problem"),
                                      second=_second(verdict="not_done"))}
    assert proofs["bed.beds_made"].status == PROBLEM
    issues = [
        OtherIssue(photo_id="P01", description="stain", room_id="bed"),
        OtherIssue(photo_id="P02", description="crack", room_id="bed"),
    ]
    kept = prune_other_issues(issues, proofs, claims)
    assert [issue.photo_id for issue in kept] == ["P02"]


def test_verdict_ready_only_when_all_critical_proven():
    critical_done = _item(id="a.x", room_id="a")
    critical_open = _item(id="b.y", room_id="b")
    optional = _item(id="c.z", room_id="c", critical=False)
    checklist = [critical_done, critical_open, optional]

    def proofs_for(status_b, status_c):
        from pot.proof import ProofItem
        return {
            "a.x": ProofItem("a.x", PROVEN, "ok", "P01"),
            "b.y": ProofItem("b.y", status_b, "r", "P02"),
            "c.z": ProofItem("c.z", status_c, "r", "P03"),
        }

    full = verdict(checklist, proofs_for(PROVEN, PROVEN))
    assert (full.ready, full.problems, full.need_photo, full.worth_a_look) == (True, [], [], [])
    bad = verdict(checklist, proofs_for(PROBLEM, NEEDS_PROOF))
    assert bad.ready is False
    assert bad.problems == ["b.y"]
    assert bad.need_photo == []
    assert bad.worth_a_look == ["c.z"]
    missing = verdict(checklist, proofs_for(NEEDS_PROOF, PROBLEM))
    assert missing.ready is False
    assert missing.problems == []
    assert missing.need_photo == ["b.y"]
    assert missing.worth_a_look == ["c.z"]  # non-critical problem never blocks


def test_evidence_images_drawn_with_caption(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    photo = photos[0]
    from pot.proof import ProofItem
    proofs = {
        "bed.beds_made": ProofItem("bed.beds_made", PROVEN, "ok", "P01"),
        "bed.other": ProofItem("bed.other", PROBLEM, "bad", "P01"),
        "bed.skip": ProofItem("bed.skip", NEEDS_PROOF, "r", ""),
    }
    items = {"bed.beds_made": _item(), "bed.other": _item(id="bed.other", key="other")}
    sams = {
        "bed.beds_made": SamCheck("bed.beds_made", "P01", "bed", 1, [_box()]),
        "bed.other": SamCheck("bed.other", "P01", "bed", 1, [_box()]),
    }
    paths = save_evidence(tmp_path, proofs, items, {"P01": photo}, sams)
    assert set(paths) == {"bed.beds_made", "bed.other"}
    with Image.open(paths["bed.beds_made"]) as img:
        assert img.size == (32, 24 + 36)
        assert img.getpixel((5, 5)) == (255, 255, 255)  # caption bar
        red, green, blue = img.getpixel((6, 36 + 10))  # green box edge (JPEG: tolerant)
        assert green > 150 and red < 60 and blue < 60
    with Image.open(paths["bed.other"]) as img:
        red, green, blue = img.getpixel((6, 36 + 10))  # red box edge
        assert red > 150 and green < 60 and blue < 60
    assert not (Path(tmp_path) / "evidence" / "bed.skip.jpg").exists()
