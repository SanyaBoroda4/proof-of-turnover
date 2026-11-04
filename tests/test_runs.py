"""Tests for pot.runs: concurrent claims, arrival-order merge, tour state."""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from PIL.Image import Exif

from pot.checklist import build_checklist
from pot.inspector import Claim
from pot.proof import NEEDS_PROOF, PROBLEM, PROVEN
from pot.runs import NOT_YET, OldPhotoError, RunState
from pot.verifier import SamCheck, SecondLook
from tests.fakes import tiny_property


def _photo_file(directory, name="pic.jpg", color="blue", taken=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    image = Image.new("RGB", (64, 48), color)
    if taken is not None:
        exif = Exif()
        exif.get_ifd(0x8769)[36867] = taken
        image.save(path, "JPEG", exif=exif)
    else:
        image.save(path, "JPEG")
    return path


def _state(directory):
    property = tiny_property()
    checklist = build_checklist(property)
    tour = {"shots": []}
    for room_id in ("bed", "bath"):
        items = [i.id for i in checklist if i.room_id == room_id and i.key != "coverage"]
        tour["shots"].append({
            "shot_id": f"{room_id}-1", "room_id": room_id, "item_ids": items,
            "instruction_es": "Foto", "instruction_en": "Photo",
        })
    run_dir = Path(directory) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunState(run_dir, property, checklist, tour=tour)


def _fresh_done(photo_id, item_ids):
    return {
        "claims": {i: Claim(i, "done", photo_id, "looks good", 1) for i in item_ids},
        "seconds": {
            i: SecondLook(i, photo_id, "yes", "done", 5, "fine", "") for i in item_ids
        },
        "sams": {i: SamCheck(i, photo_id, "thing", 3, []) for i in item_ids},
        "problem_sams": {},
        "issues": [],
        "notes": [],
        "fresh_ids": list(item_ids),
        "decisions": {},
    }


def _fresh_problem(photo_id, item_id, reason):
    return {
        "claims": {item_id: Claim(item_id, "problem", photo_id, reason, None)},
        "seconds": {
            item_id: SecondLook(item_id, photo_id, "yes", "not_done", None, reason, "mess")
        },
        "sams": {},
        "problem_sams": {item_id: SamCheck(item_id, photo_id, "mess", 1, [])},
        "issues": [],
        "notes": [],
        "fresh_ids": [item_id],
        "decisions": {},
    }


def _fresh_unclear(photo_id, item_id):
    return {
        "claims": {item_id: Claim(item_id, "done", photo_id, "maybe", None)},
        "seconds": {
            item_id: SecondLook(item_id, photo_id, "unclear", "unclear", None, "", "")
        },
        "sams": {},
        "problem_sams": {},
        "issues": [],
        "notes": [],
        "fresh_ids": [item_id],
        "decisions": {},
    }


def _bed_item(state):
    return next(i.id for i in state.checklist if i.room_id == "bed" and i.key != "coverage")


def _bath_item(state):
    return next(i.id for i in state.checklist if i.room_id == "bath" and i.key != "coverage")


def _overview_state(directory):
    property = tiny_property()
    checklist = build_checklist(property)
    bed_item = next(
        i.id for i in checklist if i.room_id == "bed" and i.key != "coverage")
    tour = {"shots": [
        {"shot_id": "bed-overview", "room_id": "bed",
         "item_ids": [bed_item], "overview": True,
         "instruction_es": "Foto", "instruction_en": "Photo"},
        {"shot_id": "bed-detail", "room_id": "bed",
         "item_ids": [bed_item],
         "instruction_es": "Foto", "instruction_en": "Photo"},
    ]}
    run_dir = Path(directory) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunState(run_dir, property, checklist, tour=tour), bed_item


def test_overview_problem_stays_and_detail_proven_beats_it(tmp_path):
    # Problem flagged on the overview photo, detail never proves it.
    state, item_id = _overview_state(tmp_path / "a")
    overview = _photo_file(tmp_path / "up-a", "a.jpg", "blue")
    photo, _ = state.claim_photo(str(overview), "bed-overview")
    state.merge_result(photo.id, _fresh_problem(photo.id, item_id, "messy"))
    assert state.proofs[item_id].status == PROBLEM

    # Same, but the detail photo proves it afterwards: proven wins.
    state, item_id = _overview_state(tmp_path / "b")
    overview = _photo_file(tmp_path / "up-b", "a.jpg", "blue")
    photo, _ = state.claim_photo(str(overview), "bed-overview")
    state.merge_result(photo.id, _fresh_problem(photo.id, item_id, "messy"))
    assert state.proofs[item_id].status == PROBLEM
    detail = _photo_file(tmp_path / "up-b2", "b.jpg", "green")
    photo2, _ = state.claim_photo(str(detail), "bed-detail")
    state.merge_result(photo2.id, _fresh_done(photo2.id, [item_id]))
    assert state.proofs[item_id].status == PROVEN
    assert state.proofs[item_id].photo_id == photo2.id


def test_older_overview_finishing_last_keeps_closeup_boxes(tmp_path):
    state, item_id = _overview_state(tmp_path)
    state.tour["shots"][0]["overview"] = False  # plain shots: Step 52 rule
    overview = _photo_file(tmp_path / "up1", "a.jpg", "blue")
    closeup = _photo_file(tmp_path / "up2", "b.jpg", "green")
    overview_photo, _ = state.claim_photo(str(overview), "bed-overview")
    closeup_photo, _ = state.claim_photo(str(closeup), "bed-detail")
    closeup_boxes = [{"left": 1, "top": 2, "right": 3, "bottom": 4}]
    overview_boxes = [{"left": 9, "top": 9, "right": 10, "bottom": 10}]
    # The newer close-up finishes first: PROBLEM citing the close-up.
    fresh_close = _fresh_problem(closeup_photo.id, item_id, "mess close")
    fresh_close["problem_sams"][item_id] = SamCheck(
        item_id, closeup_photo.id, "mess", 1, closeup_boxes)
    state.merge_result(closeup_photo.id, fresh_close)
    assert state.proofs[item_id].photo_id == closeup_photo.id
    assert state.problem_sams[item_id].boxes == closeup_boxes
    # The older overview finishes last: it must not move the proof
    # or replace the close-up's boxes with its own.
    fresh_over = _fresh_problem(overview_photo.id, item_id, "mess wide")
    fresh_over["problem_sams"][item_id] = SamCheck(
        item_id, overview_photo.id, "mess", 1, overview_boxes)
    state.merge_result(overview_photo.id, fresh_over)
    assert state.proofs[item_id].status == PROBLEM
    assert state.proofs[item_id].photo_id == closeup_photo.id
    assert state.problem_sams[item_id].photo_id == closeup_photo.id
    assert state.problem_sams[item_id].boxes == closeup_boxes


def test_problem_boxes_dropped_when_proof_moves_to_boxless_photo(tmp_path):
    state, item_id = _overview_state(tmp_path)
    state.tour["shots"][0]["overview"] = False  # plain shots: Step 52 rule
    first = _photo_file(tmp_path / "up1", "a.jpg", "blue")
    photo1, _ = state.claim_photo(str(first), "bed-overview")
    fresh1 = _fresh_problem(photo1.id, item_id, "mess")
    fresh1["problem_sams"][item_id] = SamCheck(
        item_id, photo1.id, "mess", 1,
        [{"left": 1, "top": 1, "right": 2, "bottom": 2}])
    state.merge_result(photo1.id, fresh1)
    assert state.problem_sams[item_id].boxes
    second = _photo_file(tmp_path / "up2", "b.jpg", "green")
    photo2, _ = state.claim_photo(str(second), "bed-detail")
    fresh2 = _fresh_problem(photo2.id, item_id, "mess again")
    fresh2["problem_sams"] = {}
    state.merge_result(photo2.id, fresh2)
    assert state.proofs[item_id].photo_id == photo2.id
    assert item_id not in state.problem_sams


def _fresh_mixed(photo_id, problems, unclear):
    claims = {}
    seconds = {}
    for item_id in problems:
        claims[item_id] = Claim(item_id, "problem", photo_id, "mess", None)
        seconds[item_id] = SecondLook(
            item_id, photo_id, "yes", "not_done", None, "mess", "mess")
    for item_id in unclear:
        claims[item_id] = Claim(item_id, "done", photo_id, "maybe", None)
        seconds[item_id] = SecondLook(
            item_id, photo_id, "yes", "unclear", None, "", "")
    return {
        "claims": claims, "seconds": seconds, "sams": {},
        "problem_sams": {
            i: SamCheck(i, photo_id, "mess", 1, []) for i in problems
        },
        "issues": [], "notes": [],
        "fresh_ids": list(problems) + list(unclear), "decisions": {},
    }


def test_card_groups_merge_problem_and_not_visible_on_one_photo(tmp_path):
    state = _state(tmp_path)
    bed_items = [
        i.id for i in state.checklist
        if i.room_id == "bed" and i.key != "coverage"
    ]
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_mixed(
        photo.id, bed_items[:2], bed_items[2:]))
    state.finish_photo(photo.id)
    status = state.status()
    groups = status["card_groups"]
    bed_group = next(g for g in groups if g["photo_id"] == photo.id)
    assert bed_group["item_ids"] == bed_items
    assert bed_group["shot_id"] == "bed-1"
    assert set(status["cards"]) == set(bed_items)


def test_card_groups_no_photo_item_is_alone_and_order_by_tour(tmp_path):
    property = tiny_property()
    checklist = build_checklist(property)
    beds = next(i.id for i in checklist if i.key == "beds_made")
    tour = {"shots": [{
        "shot_id": "bed-1", "room_id": "bed", "item_ids": [beds],
        "instruction_es": "Foto", "instruction_en": "Photo",
    }]}
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir, property, checklist, tour=tour)
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, beds, "mess"))
    state.finish_photo(photo.id)
    groups = state.status()["card_groups"]
    assert groups[0]["item_ids"] == [beds]
    assert groups[0]["shot_id"] == "bed-1"
    alone = [g for g in groups if len(g["item_ids"]) == 1 and not g["photo_id"]]
    assert alone
    assert all(g["key"] == g["item_ids"][0] for g in alone)


def _problem_with_boxes(photo_id, item_id, reason, boxes):
    fresh = _fresh_problem(photo_id, item_id, reason)
    fresh["problem_sams"][item_id] = SamCheck(
        item_id, photo_id, "mess", 1, boxes)
    return fresh


def test_overview_problem_wins_citation_in_both_orders(tmp_path):
    overview_boxes = [{"left": 1, "top": 1, "right": 5, "bottom": 5}]
    closeup_boxes = [{"left": 9, "top": 9, "right": 12, "bottom": 12}]
    for first in ("overview", "closeup"):
        state, item_id = _overview_state(tmp_path / first)
        over_file = _photo_file(tmp_path / f"o-{first}", "a.jpg", "blue")
        close_file = _photo_file(tmp_path / f"c-{first}", "b.jpg", "green")
        over, _ = state.claim_photo(str(over_file), "bed-overview")
        close, _ = state.claim_photo(str(close_file), "bed-detail")
        merges = [
            (over, _problem_with_boxes(over.id, item_id, "wide mess", overview_boxes)),
            (close, _problem_with_boxes(close.id, item_id, "close mess", closeup_boxes)),
        ]
        if first == "closeup":
            merges.reverse()
        for photo, fresh in merges:
            state.merge_result(photo.id, fresh)
        state.finish_photo(over.id)
        state.finish_photo(close.id)
        assert state.proofs[item_id].status == PROBLEM
        assert state.proofs[item_id].photo_id == over.id, first
        assert state.proofs[item_id].reason == "wide mess", first
        assert state.problem_sams[item_id].boxes == overview_boxes, first
        groups = [
            g for g in state.status()["card_groups"]
            if item_id in g["item_ids"]
        ]
        assert len(groups) == 1
        assert groups[0]["photo_id"] == over.id


def test_overview_problem_then_closeup_proven_no_before(tmp_path):
    state, item_id = _overview_state(tmp_path)
    over, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-overview")
    state.merge_result(over.id, _fresh_problem(over.id, item_id, "wide mess"))
    close, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u2", "b.jpg")), "bed-detail")
    state.merge_result(close.id, _fresh_done(close.id, [item_id]))
    assert state.proofs[item_id].status == PROVEN
    assert state.proofs[item_id].photo_id == close.id
    before = state.run_dir / "evidence" / f"{item_id}.before.jpg"
    assert not before.exists()


def test_newer_needs_proof_does_not_block_older_proven(tmp_path):
    state, item_id = _overview_state(tmp_path)
    over, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-overview")
    close, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u2", "b.jpg")), "bed-detail")
    # The newer close-up finishes first with NEEDS_PROOF...
    state.merge_result(close.id, _fresh_unclear(close.id, item_id))
    assert state.proofs[item_id].status == NEEDS_PROOF
    # ...then the older overview's PROVEN arrives: it must win.
    state.merge_result(over.id, _fresh_done(over.id, [item_id]))
    assert state.proofs[item_id].status == PROVEN
    assert state.proofs[item_id].photo_id == over.id


def _two_problem_group(tmp_path, box_a, box_b, same_text=False):
    state = _state(tmp_path)
    bed = [i for i in state.checklist if i.room_id == "bed" and i.key != "coverage"]
    first, second = bed[0].id, bed[1].id
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    fresh = {
        "claims": {
            first: Claim(first, "problem", photo.id, "mess", None),
            second: Claim(second, "problem", photo.id, "mess", None),
        },
        "seconds": {
            i: SecondLook(i, photo.id, "yes", "not_done", None, "m", "m")
            for i in (first, second)
        },
        "sams": {},
        "problem_sams": {
            first: SamCheck(first, photo.id, "mess", 1, [box_a]),
            second: SamCheck(second, photo.id, "mess", 1, [box_b]),
        },
        "issues": [], "notes": [],
        "fresh_ids": [first, second], "decisions": {},
    }
    state.merge_result(photo.id, fresh)
    state.finish_photo(photo.id)
    if same_text:
        for item_id in (first, second):
            state.requests[item_id] = {
                "item_id": item_id, "status": "problem",
                "cleaner": "Clear the mess", "host": "Clear the mess",
            }
    groups = [g for g in state.status()["card_groups"]
              if g["photo_id"] == photo.id]
    assert len(groups) == 1
    return groups[0], first, second, bed[2].id


def test_group_one_line_when_boxes_overlap(tmp_path):
    box = {"left": 10, "top": 10, "right": 60, "bottom": 60}
    group, first, second, third = _two_problem_group(tmp_path, box, dict(box))
    assert group["item_ids"] == [first, second, third]
    assert group["line_item_ids"] == [first, third]


def test_group_two_lines_when_boxes_far_apart(tmp_path):
    group, first, second, third = _two_problem_group(
        tmp_path,
        {"left": 10, "top": 10, "right": 60, "bottom": 60},
        {"left": 400, "top": 400, "right": 460, "bottom": 460})
    assert group["line_item_ids"] == [first, second, third]


def test_group_identical_request_texts_show_once(tmp_path):
    group, first, second, third = _two_problem_group(
        tmp_path,
        {"left": 10, "top": 10, "right": 60, "bottom": 60},
        {"left": 400, "top": 400, "right": 460, "bottom": 460},
        same_text=True)
    assert group["line_item_ids"] == [first, third]


def test_concurrent_claims_get_distinct_ids_and_both_survive(tmp_path):
    state = _state(tmp_path)
    first = _photo_file(tmp_path / "up1", "a.jpg", "blue")
    second = _photo_file(tmp_path / "up2", "b.jpg", "green")
    bed_item = _bed_item(state)
    bath_item = _bath_item(state)
    results = {}

    def claim(name, upload, shot):
        results[name] = state.claim_photo(str(upload), shot)

    threads = [
        threading.Thread(target=claim, args=("a", first, "bed-1")),
        threading.Thread(target=claim, args=("b", second, "bath-1")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results["a"][0].id != results["b"][0].id
    state.merge_result(results["a"][0].id, _fresh_done(results["a"][0].id, [bed_item]))
    state.merge_result(results["b"][0].id, _fresh_done(results["b"][0].id, [bath_item]))
    assert state.proofs[bed_item].status == PROVEN
    assert state.proofs[bath_item].status == PROVEN
    saved = json.loads((state.run_dir / "first_look.json").read_text(encoding="utf-8"))
    assert len(saved["photos"]) == 2


def test_older_photo_finishing_last_does_not_overwrite(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    state.merge_result(second.id, _fresh_problem(second.id, item, "mess everywhere"))
    state.merge_result(first.id, _fresh_problem(first.id, item, "a stain"))
    proof = state.proofs[item]
    assert (proof.status, proof.photo_id, proof.reason) == (
        PROBLEM, second.id, "mess everywhere")


def test_unclear_result_never_erases_proven_or_problem(tmp_path):
    state = _state(tmp_path)
    proven_item = _bed_item(state)
    problem_item = _bath_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(first.id, _fresh_done(first.id, [proven_item]))
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg")), "bath-1")
    state.merge_result(second.id, _fresh_problem(second.id, problem_item, "dirty"))
    third, _ = state.claim_photo(str(_photo_file(tmp_path / "u3", "c.jpg", "red")), "bed-1")
    state.merge_result(third.id, _fresh_unclear(third.id, proven_item))
    fourth, _ = state.claim_photo(str(_photo_file(tmp_path / "u4", "d.jpg", "red")), "bath-1")
    state.merge_result(fourth.id, _fresh_unclear(fourth.id, problem_item))
    assert state.proofs[proven_item].status == PROVEN
    assert state.proofs[proven_item].photo_id == first.id
    assert state.proofs[problem_item].status == PROBLEM
    assert state.proofs[problem_item].photo_id == second.id


def test_newer_problem_replaces_proven(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(first.id, _fresh_done(first.id, [item]))
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    merged = state.merge_result(second.id, _fresh_problem(second.id, item, "now dirty"))
    assert merged["changes"][item] == (PROVEN, PROBLEM)
    assert state.proofs[item].photo_id == second.id


def test_status_marks_untaken_shots_not_yet(tmp_path):
    state = _state(tmp_path)
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    assert all(i["status"] == NOT_YET for i in bed["items"] if i["id"] != "bed.coverage")
    assert status["agent_state"] == "needs_input"
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [_bed_item(state)]))
    status = state.status()
    bath = next(r for r in status["rooms"] if r["id"] == "bath")
    assert all(i["status"] == NOT_YET for i in bath["items"] if i["id"] != "bath.coverage")
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    assert bed["items"][1]["status"] == PROVEN
    rest = [i for i in bed["items"][2:] if i["status"] == "needs photo"]
    assert rest and all(i["reason"] == "No photo shows it" for i in rest)
    assert status["progress"]["shots_taken"] == 1
    assert status["current_shot"]["shot_id"] == "bath-1"


def test_photo_stays_checking_until_finished(tmp_path):
    state = _state(tmp_path)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    assert photo.id in state.status()["checking"]
    state.merge_result(photo.id, _fresh_done(photo.id, [_bed_item(state)]))
    assert photo.id in state.status()["checking"]
    state.finish_photo(photo.id)
    assert photo.id not in state.status()["checking"]


def test_coverage_proven_at_claim_before_merge(tmp_path):
    state = _state(tmp_path)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    assert state.proofs["bed.coverage"].status == PROVEN
    assert state.proofs["bed.coverage"].photo_id == photo.id
    assert state.proofs["bath.coverage"].status == NEEDS_PROOF


def test_old_photo_refused_but_missing_or_new_time_allowed(tmp_path):
    state = _state(tmp_path)
    old = (datetime.now() - timedelta(days=1)).strftime("%Y:%m:%d %H:%M:%S")
    with pytest.raises(OldPhotoError, match="foto antigua"):
        state.claim_photo(str(_photo_file(tmp_path / "u1", "old.jpg", taken=old)), "bed-1")
    fresh = (state.started_at + timedelta(seconds=5)).strftime("%Y:%m:%d %H:%M:%S")
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u2", "new.jpg", taken=fresh)), "bed-1"
    )
    assert photo.id == "P01"
    photo2, _ = state.claim_photo(str(_photo_file(tmp_path / "u3", "plain.jpg")), "bed-1")
    assert photo2.id == "P02"


def test_claim_unknown_shot_raises(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError, match="Unknown shot"):
        state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "nope")


def test_send_back_returns_items_to_needs_photo(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [item]))
    signoff = state.send_back([item], "Hazlo de nuevo")
    assert state.proofs[item].status == NEEDS_PROOF
    assert state.proofs[item].reason == "Hazlo de nuevo"
    assert state.agent_state() == "needs_input"
    assert signoff["decision"] == "sendback"
    assert signoff["items"] == [item]
    assert item in signoff["open_items"]
    saved = json.loads((state.run_dir / "signoff.json").read_text(encoding="utf-8"))
    assert saved["decision"] == "sendback"


def test_approve_marks_done(tmp_path):
    state = _state(tmp_path)
    signoff = state.approve()
    assert state.agent_state() == "done"
    assert signoff["decision"] == "approve"
    assert signoff["time"]


def test_older_photo_stores_nothing_when_verdict_kept(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    state.merge_result(second.id, _fresh_problem(second.id, item, "mess everywhere"))
    state.merge_result(first.id, _fresh_problem(first.id, item, "a stain"))
    assert state.claims[item].photo_id == second.id
    assert state.seconds[item].reason == "mess everywhere"
    saved = json.loads((state.run_dir / "proof.json").read_text(encoding="utf-8"))
    entry = saved["items"][item]
    assert entry["claim"]["photo_id"] == second.id
    assert entry["second_look"]["photo_id"] == second.id
    assert entry["second_look"]["reason"] == "mess everywhere"


def test_unclear_result_keeps_old_second_look(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(first.id, _fresh_done(first.id, [item]))
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    state.merge_result(second.id, _fresh_unclear(second.id, item))
    assert state.seconds[item].photo_id == first.id
    assert state.seconds[item].verdict == "done"
    saved = json.loads((state.run_dir / "proof.json").read_text(encoding="utf-8"))
    assert saved["items"][item]["second_look"]["photo_id"] == first.id


def test_old_photo_allows_15_minutes_clock_grace(tmp_path):
    state = _state(tmp_path)
    close = (state.started_at - timedelta(minutes=5)).strftime("%Y:%m:%d %H:%M:%S")
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg", taken=close)), "bed-1"
    )
    assert photo.id == "P01"
    far = (state.started_at - timedelta(minutes=16)).strftime("%Y:%m:%d %H:%M:%S")
    with pytest.raises(OldPhotoError, match="foto antigua"):
        state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", taken=far)), "bed-1")


def test_news_reports_changes_requests_and_signoff(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess everywhere"))
    state.store_requests(
        [{"item_id": item, "room_id": "bed", "room_name": "Bed",
          "status": PROBLEM, "cleaner": "Limpia", "host": "Clean"}], [],
    )
    state.approve()
    news = state.status()["news"]
    assert [e["kind"] for e in news] == ["signoff", "request", "status"]
    assert news[0]["text"] == "Host approved the turnover"
    assert news[1]["item_id"] == item and news[1]["photo_id"] == photo.id
    assert news[1]["text"] == "request sent: Clean"
    assert news[2]["status"] == PROBLEM and news[2]["room_name"] == "Bed"
    assert news[2]["photo_id"] == photo.id
    import re

    assert all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", e["time"]) for e in news)


def test_load_keeps_language(tmp_path):
    from pot.checklist import load_property

    property = load_property("demo-house")
    checklist = build_checklist(property)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RunState(run_dir, property, checklist, tour={"shots": []}, language="ru")
    with state.lock:
        state._save_locked()
    assert RunState.load(run_dir).language == "ru"


def test_load_without_language_gets_property_default(tmp_path):
    from pot.checklist import load_property

    property = load_property("demo-house")
    checklist = build_checklist(property)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RunState(run_dir, property, checklist, tour={"shots": []}, language="ru")
    with state.lock:
        state._save_locked()
    first_path = run_dir / "first_look.json"
    data = json.loads(first_path.read_text(encoding="utf-8"))
    del data["language"]  # runs saved before languages existed
    first_path.write_text(json.dumps(data), encoding="utf-8")
    assert RunState.load(run_dir).language == "es"


def test_has_evidence_only_when_file_exists(tmp_path):
    state = _state(tmp_path)
    bed_item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    coverage = next(i for i in bed["items"] if i["id"] == "bed.coverage")
    assert coverage["status"] == "proven"
    assert coverage["has_evidence"] is False
    state.merge_result(photo.id, _fresh_done(photo.id, [bed_item]))
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    item = next(i for i in bed["items"] if i["id"] == bed_item)
    assert item["has_evidence"] is True
    state.send_back([bed_item], "again")
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    item = next(i for i in bed["items"] if i["id"] == bed_item)
    assert item["has_evidence"] is False


def test_load_roundtrip_restores_state(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    property = load_property("demo-house")
    checklist = build_checklist(property)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir, property, checklist,
                     tour={"shots": load_tour("demo-house")})
    item = "master-bedroom.beds_made"
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "master-bedroom-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [item]))
    state.record_call({"purpose": "second look", "model": "m", "photo_id": photo.id,
                       "seconds": 1.0, "attempts": 1, "source": "live"})
    (state.run_dir / "calls.jsonl").write_text(
        json.dumps(state.calls[0]) + "\n", encoding="utf-8"
    )
    loaded = RunState.load(state.run_dir)
    assert loaded.proofs[item].status == PROVEN
    assert loaded.coverage["master-bedroom"] == "covered"
    assert loaded.taken_shots() == ["master-bedroom-1"]
    assert loaded.call_log()[0]["purpose"] == "second look"
    assert loaded.status()["progress"]["photos"] == 1
    # Arrival order survives the restart: an older result stays ignored.
    assert loaded.decided_seq[item] == 0


def _timeline(state):
    path = state.run_dir / "timeline.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _status_item(status, item_id):
    for room in status["rooms"]:
        for item in room["items"]:
            if item["id"] == item_id:
                return item
    raise AssertionError(f"no {item_id} in status")


def test_timeline_records_round_from_receive_to_all_checked(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [item]))
    state.finish_photo(photo.id)
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg")), "bath-1")
    state.merge_result(second.id, _fresh_done(second.id, [_bath_item(state)]))
    state.finish_photo(second.id)
    events = _timeline(state)
    kinds = [entry["event"] for entry in events]
    assert kinds.count("photo_received") == 2
    assert kinds.count("photo_finished") == 2
    assert kinds.index("round_done") > kinds.index("photo_received")
    assert kinds[-1] == "all_checked"
    assert any(entry["event"] == "card_ready" for entry in events)
    assert all("." in entry["time"] for entry in events)  # ms timestamps
    assert events[0]["photo_id"] == "P01"
    assert events[0]["shot_id"] == "bed-1"


def test_cards_open_critical_items_in_tour_order(tmp_path):
    state = _state(tmp_path)
    assert state.status()["cards"] == []
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [item]))
    assert state.status()["cards"] == []  # shot still checking
    state.finish_photo(photo.id)
    bed_shot = next(s for s in state.tour["shots"] if s["shot_id"] == "bed-1")
    expected = [
        item_id for item_id in bed_shot["item_ids"]
        if item_id != item and state.items_by_id[item_id].critical
    ]
    assert state.status()["cards"] == expected
    assert expected  # the tour really has other critical bed items


def test_resolve_item_stores_and_counts_for_agent_state(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    assert state.agent_state() == "needs_input"
    resolution = state.resolve_item(item, "cant_fix", "supplies", None)
    assert resolution["action"] == "cant_fix"
    assert resolution["reason"] == "supplies"
    assert _status_item(state.status(), item)["resolution"] == resolution
    assert item not in state.status()["cards"]
    for other in state.checklist:
        if other.critical and state.proofs[other.id].status != PROVEN:
            state.resolve_item(other.id, "its_fine", None, None)
    assert state.agent_state() == "awaiting_signoff"
    assert [entry["event"] for entry in _timeline(state)].count("card_resolved") >= 1


def test_retake_proving_item_clears_resolution(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    state.resolve_item(item, "cant_fix", "broken", None)
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg")), "bed-1")
    state.merge_result(second.id, _fresh_done(second.id, [item]))
    state.finish_photo(second.id)
    assert state.proofs[item].status == PROVEN
    assert item not in state.resolutions
    assert _status_item(state.status(), item)["resolution"] is None


def test_tour_photo_proven_after_problem_creates_no_before(tmp_path):
    # Overview PROBLEM, then a later tour (close-up) photo proves it:
    # nobody "fixed" anything, so no before image.
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg")), "bed-1")
    state.merge_result(second.id, _fresh_done(second.id, [item]))
    state.finish_photo(second.id)
    assert state.proofs[item].status == PROVEN
    before_path = state.run_dir / "evidence" / f"{item}.before.jpg"
    assert not before_path.exists()
    assert _status_item(state.status(), item)["has_before"] is False


def test_before_image_kept_once_on_problem_to_proven(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    before_path = state.run_dir / "evidence" / f"{item}.before.jpg"
    assert not before_path.exists()
    second, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u2", "b.jpg")), "bed-1",
        retake_items=[item])
    state.merge_result(second.id, _fresh_done(second.id, [item]))
    state.finish_photo(second.id)
    assert state.proofs[item].status == PROVEN
    first_bytes = before_path.read_bytes()
    assert _status_item(state.status(), item)["has_before"] is True
    third, _ = state.claim_photo(str(_photo_file(tmp_path / "u3", "c.jpg")), "bed-1")
    state.merge_result(third.id, _fresh_problem(third.id, item, "mess again"))
    state.finish_photo(third.id)
    fourth, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u4", "d.jpg")), "bed-1",
        retake_items=[item])
    state.merge_result(fourth.id, _fresh_done(fourth.id, [item]))
    state.finish_photo(fourth.id)
    assert before_path.read_bytes() == first_bytes  # never overwritten


def test_retake_items_survive_load(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    run_dir = tmp_path / "run"
    state = RunState.create(
        run_dir, "demo-house", tour={"shots": load_tour("demo-house")})
    item = "master-bedroom.beds_made"
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg")), "master-bedroom-1",
        retake_items=[item])
    assert state.photo_retake[photo.id] == [item]
    loaded = RunState.load(state.run_dir)
    assert loaded.photo_retake[photo.id] == [item]
    saved = json.loads(
        (state.run_dir / "first_look.json").read_text(encoding="utf-8"))
    entry = next(e for e in saved["photos"] if e["id"] == photo.id)
    assert entry["retake_items"] == [item]


def test_shot_without_critical_items_finishes(tmp_path):
    state = _state(tmp_path)
    state.tour["shots"].append(
        {"shot_id": "extra-1", "room_id": "bed", "item_ids": []}
    )
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "extra-1")
    empty = {
        "claims": {}, "seconds": {}, "sams": {}, "problem_sams": {},
        "issues": [], "notes": [], "fresh_ids": [], "decisions": {},
    }
    state.merge_result(photo.id, empty)
    state.finish_photo(photo.id)
    assert state.status()["checking"] == []
    assert state.status()["cards"] == []
    assert state.finish()


def test_resolutions_and_finish_survive_reload(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    property = load_property("demo-house")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir, property, build_checklist(property),
                     tour={"shots": load_tour("demo-house")})
    item = "master-bedroom.beds_made"
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u", "a.jpg")), "master-bedroom-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    state.resolve_item(item, "cant_fix", "broken", None)
    finished_at = state.finish()
    loaded = RunState.load(state.run_dir)
    assert loaded.resolutions[item]["action"] == "cant_fix"
    assert loaded.finished_at == finished_at
    assert loaded.status()["finished_at"] == finished_at
    assert loaded.card_ready_items  # seeded from timeline.jsonl
    assert loaded.agent_state() == "needs_input"


def test_resolution_news_carries_action_reason_and_labels(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    state.resolve_item(item, "cant_fix", "supplies", None)
    other = _bath_item(state)
    state.resolve_item(other, "its_fine", None, None)
    status = state.status()
    news = {
        event["item_id"]: event for event in status["news"]
        if event["kind"] == "resolution"
    }
    assert news[item]["status"] == "cant_fix"
    assert news[item]["reason"] == "supplies"
    assert news[item]["item_label"] == state.items_by_id[item].label
    assert news[item]["text"] == f"Cleaner reported {item} (supplies)"
    assert news[other]["status"] == "its_fine"
    assert news[other]["reason"] is None
    assert all(
        "reason" not in event for event in status["news"]
        if event["kind"] != "resolution"
    )
    resolution = _status_item(status, item)["resolution"]
    assert (resolution["action"], resolution["reason"]) == ("cant_fix", "supplies")


def test_send_back_clears_resolution_and_reopens_run(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess"))
    state.finish_photo(photo.id)
    state.resolve_item(item, "its_fine", None, None)
    state.finish()
    state.send_back([item], "Try again")
    assert item not in state.resolutions
    assert state.finished_at is None
    assert state.status()["finished_at"] is None
    assert state.agent_state() == "needs_input"


def test_pick_failures_do_not_make_photo_unchecked(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    prop = load_property("demo-house")
    checklist = build_checklist(prop)
    state = RunState(tmp_path / "run", prop, checklist,
                     tour={"shots": load_tour("demo-house")})
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "p", "a.jpg")), "master-bedroom-1")

    def failed(purpose):
        return {"purpose": purpose, "model": "m", "photo_id": photo.id,
                "seconds": 0.4, "attempts": 2, "source": "failed",
                "status": 503}

    assert not state.photo_had_api_failure(photo.id, 0)
    state.record_call(failed("pick problem boxes bed.beds_made P01 (shoe)"))
    state.record_call(failed("pick problem area bed.floor_clear P01"))
    state.record_call(failed("second opinion bed.beds_made P01"))
    assert not state.photo_had_api_failure(photo.id, 0)
    state.record_call(failed("second look bed.beds_made P01"))
    assert state.photo_had_api_failure(photo.id, 0)


def test_second_opinion_does_not_change_api_down(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    prop = load_property("demo-house")
    state = RunState(tmp_path / "run", prop, build_checklist(prop),
                     tour={"shots": load_tour("demo-house")})

    def record(purpose, source, status=None):
        entry = {"purpose": purpose, "model": "m", "photo_id": "P01",
                 "seconds": 0.4, "attempts": 1, "source": source}
        if status is not None:
            entry["status"] = status
        state.record_call(entry)

    record("second opinion bed.beds_made P01", "failed", 503)
    assert state.api_down is None
    record("second look bed.beds_made P01", "failed", 503)
    assert state.api_down == "Meta API error (503)"
    record("second opinion bed.beds_made P01", "live")
    assert state.api_down == "Meta API error (503)"


def test_unchecked_and_skipped_survive_reload(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    property = load_property("demo-house")
    checklist = build_checklist(property)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir, property, checklist,
                     tour={"shots": load_tour("demo-house")})
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u", "a.jpg")), "master-bedroom-1")
    failed = {"purpose": "first look", "model": "m", "photo_id": photo.id,
              "seconds": 0.4, "attempts": 1, "source": "failed", "status": 401}
    state.record_call(dict(failed))
    state.mark_unchecked(photo.id, "AI check failed, saved for retry")
    assert len(state.end_round()) == 10
    assert state.status()["cards"] == []
    (state.run_dir / "calls.jsonl").write_text(
        json.dumps(failed) + "\n", encoding="utf-8"
    )
    loaded = RunState.load(state.run_dir)
    assert loaded.unchecked == {photo.id}
    assert loaded.round_ended is True
    assert len(loaded.skipped_shots) == 10
    assert loaded.api_down == "key rejected (401)"
    assert loaded.status()["cards"] == []
    assert loaded.status()["unchecked"] == [photo.id]
