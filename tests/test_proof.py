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
           coverage="covered", first_photo="P01", decision="none", rooms=None,
           candidates=()):
    item = _item() if item is _DEFAULT else item
    claim = _claim() if claim is _DEFAULT else claim
    second = _second() if second is _DEFAULT else second
    sam = _sam() if sam is _DEFAULT else sam
    return prove_item(item, claim, second, sam, unknown, coverage, first_photo,
                      decision, rooms, candidates)


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


def _rooms():
    return {
        "bed": {"id": "bed", "name": "Bed"},
        "bath": {"id": "bath", "name": "Bath"},
    }


def test_done_unknown_photo_uses_room_decision():
    decided = _prove(unknown=True, decision="bed", rooms=_rooms())
    assert decided.status == PROVEN
    other = _prove(unknown=True, decision="bath", rooms=_rooms())
    assert other.status == NEEDS_PROOF
    assert other.reason == "This photo looks like the Bath"
    missing = _prove(unknown=True, rooms=_rooms())
    assert missing.status == NEEDS_PROOF
    assert missing.reason == "Can't tell which room this photo shows"


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


def test_problem_undecided_room_reported_once_with_candidates():
    second = _second(verdict="not_done", this_room="unclear")
    bath_item = _item(id="bath.no_guest_items", room_id="bath", room_name="Bath",
                      key="no_guest_items")
    first = _prove(item=bath_item, claim=_claim(status="problem"), second=second,
                   unknown=True, decision="none", rooms=_rooms(),
                   candidates=("bath", "bed"))
    assert first.status == PROBLEM
    assert first.reason == "room not confirmed: Bath or Bed"
    other = _prove(claim=_claim(status="problem"), second=second, unknown=True,
                   decision="none", rooms=_rooms(), candidates=("bath", "bed"))
    assert other.status == NEEDS_PROOF
    assert other.reason == "Same problem reported for the Bath"


def test_problem_undecided_room_single_candidate():
    second = _second(verdict="not_done", this_room="unclear")
    proof = _prove(claim=_claim(status="problem"), second=second, unknown=True,
                   decision="none", rooms=_rooms(), candidates=("bed",))
    assert proof.status == PROBLEM
    assert proof.reason == "room not confirmed: Bed"


def test_problem_undecided_room_unconfirmed_stays_needs_proof():
    proof = _prove(claim=_claim(status="problem"), second=_second(verdict="unclear"),
                   unknown=True, decision="none", rooms=_rooms(), candidates=("bed",))
    assert proof.status == NEEDS_PROOF
    assert "disagree" in proof.reason
    proof = _prove(claim=_claim(status="problem"),
                   second=_second(verdict="not_done", this_room="no"),
                   unknown=True, decision="none", rooms=_rooms(), candidates=("bed",))
    assert proof.status == NEEDS_PROOF
    assert "not sure this photo is the Bed" in proof.reason


def test_done_undecided_room_still_needs_proof():
    proof = _prove(unknown=True, decision="none", rooms=_rooms(), candidates=("bed",))
    assert proof.status == NEEDS_PROOF
    assert proof.reason == "Can't tell which room this photo shows"


def test_prove_all_computes_candidates_from_claims():
    items = [
        _item(),
        _item(id="bath.no_guest_items", room_id="bath", room_name="Bath",
              key="no_guest_items", sam_prompt=None, min_count=None),
    ]
    claims = {
        "bed.beds_made": _claim(status="problem"),
        "bath.no_guest_items": Claim(item_id="bath.no_guest_items", status="problem",
                                     photo_id="P01", seen="mess", count_seen=None),
    }
    seconds = {
        "bed.beds_made": _second(verdict="not_done", this_room="unclear"),
        "bath.no_guest_items": SecondLook(item_id="bath.no_guest_items", photo_id="P01",
                                          this_room="unclear", verdict="not_done",
                                          count_seen=None, reason="mess"),
    }
    proofs = prove_all(items, claims, seconds, {"bed.beds_made": _sam()}, {},
                       {}, ["P01"], {}, _rooms())
    assert proofs["bath.no_guest_items"].status == PROBLEM
    assert proofs["bath.no_guest_items"].reason == "room not confirmed: Bath or Bed"
    assert proofs["bed.beds_made"].status == NEEDS_PROOF


def test_problem_unknown_photo_uses_room_decision():
    proof = _prove(claim=_claim(status="problem"),
                   second=_second(verdict="not_done", this_room="yes"),
                   unknown=True, decision="bed", rooms=_rooms())
    assert proof.status == PROBLEM
    proof = _prove(claim=_claim(status="problem"),
                   second=_second(verdict="not_done", this_room="yes"),
                   unknown=True, decision="bath", rooms=_rooms())
    assert proof.status == NEEDS_PROOF
    assert proof.reason == "This photo looks like the Bath"


def test_problem_seen_but_room_unsure():
    proof = _prove(claim=_claim(status="problem"),
                   second=_second(verdict="not_done", this_room="no"))
    assert proof.status == NEEDS_PROOF
    assert proof.reason == "Problem seen, but not sure this photo is the Bed"


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
                       {"bed": "covered"}, ["P01", "P09"], {"P09": "bed"}, _rooms())
    assert proofs["bed.beds_made"].status == PROVEN  # unknown photo decided bed
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


def _has_color(img, pred):
    pixels = img.load()
    return any(
        pred(pixels[x, y])
        for x in range(img.width)
        for y in range(img.height)
    )


def _is_muted_blue(pixel):
    red, green, blue = pixel
    return blue > 140 and red < 110 and green < 140


def _is_red(pixel):
    red, green, blue = pixel
    return red > 150 and green < 60 and blue < 60


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
        "bed.other": SamCheck("bed.other", "P01", "bed", 1, [_box(left=20)]),
    }
    problem_sams = {"bed.other": SamCheck("bed.other", "P01", "stain", 1, [_box()])}
    paths = save_evidence(tmp_path, proofs, items, {"P01": photo}, sams, problem_sams)
    assert set(paths) == {"bed.beds_made", "bed.other"}
    with Image.open(paths["bed.beds_made"]) as img:
        assert img.size[0] == 32 and img.size[1] > 24 + 20  # caption bar added
        assert img.getpixel((2, 2)) == (255, 255, 255)
        assert _has_color(img, _is_muted_blue)
        assert not _has_color(img, _is_red)
    with Image.open(paths["bed.other"]) as img:
        assert _has_color(img, _is_red)  # problem-object boxes, not item boxes
        assert not _has_color(img, _is_muted_blue)
    assert not (Path(tmp_path) / "evidence" / "bed.skip.jpg").exists()


def test_proven_count_caption_reads_seen_needed():
    proof = _prove(sam=_sam(count=2))
    assert proof.status == PROVEN
    assert proof.reason == "Confirmed in the photo (2 seen, 1 needed)"


def test_problem_without_boxes_gets_red_frame(tmp_path):
    photos = make_photos(tmp_path / "photos", 1)
    photo = photos[0]
    from pot.proof import ProofItem
    proofs = {
        "bed.framed": ProofItem("bed.framed", PROBLEM, "bad", "P01"),
        "bed.proven": ProofItem("bed.proven", PROVEN, "ok", "P01"),
    }
    items = {"bed.framed": _item(id="bed.framed", key="framed"),
             "bed.proven": _item(id="bed.proven", key="proven")}
    problem_sams = {
        "bed.framed": SamCheck("bed.framed", "P01", "stain", 0, []),
    }
    paths = save_evidence(tmp_path, proofs, items, {"P01": photo}, {}, problem_sams)
    with Image.open(paths["bed.framed"]) as img:
        assert _has_color(img, _is_red)  # the whole-photo frame
        pixels = img.load()
        assert any(_is_red(pixels[1, y]) for y in range(13, img.height - 1))
    with Image.open(paths["bed.proven"]) as img:
        assert not _has_color(img, _is_red)  # proven items unchanged


def test_red_frame_width_scales_with_photo(tmp_path):
    from types import SimpleNamespace

    big = tmp_path / "big.jpg"
    Image.new("RGB", (1200, 1600), "blue").save(big, "JPEG")
    from pot.proof import ProofItem
    proofs = {"bed.framed": ProofItem("bed.framed", PROBLEM, "bad", "P01")}
    items = {"bed.framed": _item(id="bed.framed", key="framed")}
    problem_sams = {
        "bed.framed": SamCheck("bed.framed", "P01", "stain", 0, []),
    }
    paths = save_evidence(
        tmp_path, proofs, items, {"P01": SimpleNamespace(check_path=big)},
        {}, problem_sams)
    with Image.open(paths["bed.framed"]) as img:
        # 20 px on 1200x1600: inside the frame is red, beyond it is not.
        middle = img.height // 2
        assert _is_red(img.getpixel((15, middle)))
        assert not _is_red(img.getpixel((60, middle)))
